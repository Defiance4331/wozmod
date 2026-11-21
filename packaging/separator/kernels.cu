// The Mel-Band RoFormer network of the Dream voice separator on an NVIDIA GPU (native/src/cuda.rs runs it; its
// module docs say in which order). packaging/separator_kernels.py compiles this file to PTX with NVRTC when a release
// is built; the installer hands the PTX to the NVIDIA driver, which compiles it for the player's card. Nothing of the
// CUDA toolkit is needed on the player's machine.
//
// The matrix products run on the tensor cores, through inline PTX: ldmatrix and mma.sync.m16n8k8 (compute capability
// 7.5 and later), 16-bit float inputs, 32-bit float sums. Activations are 16-bit float rows of 384, the token of frame
// t and band b in row t * 60 + b; a weight matrix is 16-bit floats stored [outputs][inputs].
#include <cuda_fp16.h>
typedef unsigned short u16;
typedef unsigned int u32;

__device__ __forceinline__ u32 smem_u32(const void *p) { return (u32)__cvta_generic_to_shared(p); }
__device__ __forceinline__ void ldsm_x4(u32 &r0, u32 &r1, u32 &r2, u32 &r3, const void *p) {
    asm volatile("ldmatrix.sync.aligned.m8n8.x4.shared.b16 {%0,%1,%2,%3}, [%4];" : "=r"(r0), "=r"(r1), "=r"(r2), "=r"(r3) : "r"(smem_u32(p)));
}
__device__ __forceinline__ void ldsm_x4_t(u32 &r0, u32 &r1, u32 &r2, u32 &r3, const void *p) {
    asm volatile("ldmatrix.sync.aligned.m8n8.x4.trans.shared.b16 {%0,%1,%2,%3}, [%4];" : "=r"(r0), "=r"(r1), "=r"(r2), "=r"(r3) : "r"(smem_u32(p)));
}
// d (16x8 f32) += a (16x8 f16, row) * b (8x8 f16, col)
__device__ __forceinline__ void mma1688(float *d, u32 a0, u32 a1, u32 b0) {
    asm volatile("mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 {%0,%1,%2,%3}, {%4,%5}, {%6}, {%0,%1,%2,%3};"
                 : "+f"(d[0]), "+f"(d[1]), "+f"(d[2]), "+f"(d[3]) : "r"(a0), "r"(a1), "r"(b0));
}
__device__ __forceinline__ u32 pack2(float lo, float hi) {
    __half2 h = __floats2half2_rn(lo, hi); return *(u32 *)&h;
}
__device__ __forceinline__ float gelu_erf(float x) { return 0.5f * x * (1.f + erff(x * 0.70710678118654752f)); }

// ---------------------------------------------------------------------------------------------------------------
// GEMM, grouped: problem z computes C[m, n] = act(sum_k A[m, k] B[n, k] + bias[n]) (+ R[m, n]) for m < M, n < N.
// K must be a multiple of 32 (callers zero-pad), lda/ldb/ldc multiples of 8. Tile 128 x 128 x 32, 8 warps of 64 x 32.
struct Gemm {
    const u16 *A, *B, *bias, *R; u16 *C;
    int M, N, K, lda, ldb, ldc, ldr, act;  // act: 0 none, 1 gelu (erf), 2 tanh
    const u16 *rc, *rs;                   // rotary in the epilogue (qkv: columns < rope_n), caches [positions][32]
    int pdiv, pmod, rope_n, pad;          // position of row m = (m / pdiv) % pmod
};
#define BM 128
#define BN 128
#define BK 32
// Shared memory holds unpadded 64-byte rows, the 16-byte chunk of each XOR-swizzled by (row / 2) % 4: the 8 rows one
// ldmatrix reads then hit 8 distinct bank groups, and two stages of A and B take 32 KB, so two blocks fit a
// multiprocessor (padded rows took 40 KB, one block each, and ran a quarter slower).
#define SK 32
#define SOFF(r, k) ((r) * 32 + (((((k) >> 3) ^ (((r) >> 1) & 3))) << 3) + ((k) & 7))
#define GEMM_BOUNDS __launch_bounds__(256, 2)

extern "C" __global__ void GEMM_BOUNDS gemm(const Gemm *probs) {
    const Gemm p = probs[blockIdx.z];
    const int m0 = blockIdx.y * BM, n0 = blockIdx.x * BN;
    if (m0 >= p.M || n0 >= p.N) return;
    __shared__ __align__(16) u16 sA[2][BM * SK];
    __shared__ __align__(16) u16 sB[2][BN * SK];
    const int tid = threadIdx.x, warp = tid >> 5, lane = tid & 31;
    const int wm = (warp >> 2) * 64, wn = (warp & 3) * 32;  // warp tile origin in the block tile
    float acc[4][4][4];
#pragma unroll
    for (int i = 0; i < 4; i++)
#pragma unroll
        for (int j = 0; j < 4; j++)
#pragma unroll
            for (int c = 0; c < 4; c++) acc[i][j][c] = 0.f;
    // each thread moves 2 x 16 bytes of A and of B per k tile: rows tid/4 and tid/4 + 64, k chunk (tid % 4) * 8
    const int lr = tid >> 2, lc = (tid & 3) * 8;
    uint4 ra[2], rb[2];
    auto gload = [&](int k0) {
#pragma unroll
        for (int i = 0; i < 2; i++) {
            int m = m0 + lr + i * 64, n = n0 + lr + i * 64;
            ra[i] = m < p.M ? *(const uint4 *)(p.A + (size_t)m * p.lda + k0 + lc) : make_uint4(0, 0, 0, 0);
            rb[i] = n < p.N ? *(const uint4 *)(p.B + (size_t)n * p.ldb + k0 + lc) : make_uint4(0, 0, 0, 0);
        }
    };
    auto sstore = [&](int buf) {
#pragma unroll
        for (int i = 0; i < 2; i++) {
            *(uint4 *)&sA[buf][SOFF(lr + i * 64, lc)] = ra[i];
            *(uint4 *)&sB[buf][SOFF(lr + i * 64, lc)] = rb[i];
        }
    };
    const int nk = p.K / BK;
    gload(0); sstore(0); __syncthreads();
    for (int kt = 0; kt < nk; kt++) {
        const int buf = kt & 1;
        if (kt + 1 < nk) gload((kt + 1) * BK);
#pragma unroll
        for (int ks = 0; ks < BK; ks += 16) {
            u32 a[4][4], b[2][4];
            // A: 16 rows x 16 k per m tile; lanes 0-15 address rows 0-15 at k, lanes 16-31 rows 0-15 at k + 8
#pragma unroll
            for (int i = 0; i < 4; i++)
                ldsm_x4(a[i][0], a[i][1], a[i][2], a[i][3], &sA[buf][SOFF(wm + i * 16 + (lane & 15), ks + (lane >> 4) * 8)]);
            // B: two n8 tiles x 16 k per ldmatrix.x4; lanes 0-7 n 0-7 k, 8-15 n 0-7 k+8, 16-23 n 8-15 k, 24-31 n 8-15 k+8
#pragma unroll
            for (int j = 0; j < 2; j++)
                ldsm_x4(b[j][0], b[j][1], b[j][2], b[j][3],
                        &sB[buf][SOFF(wn + j * 16 + (lane & 7) + ((lane >> 4) << 3), ks + ((lane >> 3) & 1) * 8)]);
#pragma unroll
            for (int i = 0; i < 4; i++)
#pragma unroll
                for (int j = 0; j < 4; j++) {
                    // a regs: 0 = rows 0-7 k0, 1 = rows 8-15 k0, 2 = rows 0-7 k8, 3 = rows 8-15 k8
                    u32 b0 = b[j >> 1][(j & 1) * 2], b1 = b[j >> 1][(j & 1) * 2 + 1];
                    mma1688(acc[i][j], a[i][0], a[i][1], b0);
                    mma1688(acc[i][j], a[i][2], a[i][3], b1);
                }
        }
        if (kt + 1 < nk) { sstore(buf ^ 1); }
        __syncthreads();
    }
    // epilogue: thread holds rows g, g + 8 and columns 2t, 2t + 1 of each 16 x 8 tile
    const int g = lane >> 2, t = lane & 3;
#pragma unroll
    for (int i = 0; i < 4; i++)
#pragma unroll
        for (int j = 0; j < 4; j++) {
            int n = n0 + wn + j * 8 + t * 2;
            if (n >= p.N) continue;
            float b0 = 0.f, b1 = 0.f;
            if (p.bias) { b0 = __half2float(((const __half *)p.bias)[n]); b1 = __half2float(((const __half *)p.bias)[n + 1]); }
#pragma unroll
            for (int h = 0; h < 2; h++) {
                int m = m0 + wm + i * 16 + g + h * 8;
                if (m >= p.M) continue;
                float v0 = acc[i][j][h * 2] + b0, v1 = acc[i][j][h * 2 + 1] + b1;
                if (p.act == 1) { v0 = gelu_erf(v0); v1 = gelu_erf(v1); }
                else if (p.act == 2) { v0 = tanhf(v0); v1 = tanhf(v1); }
                if (p.R) {
                    __half2 r = *(const __half2 *)(p.R + (size_t)m * p.ldr + n);
                    v0 += __low2float(r); v1 += __high2float(r);
                }
                if (p.rc && n < p.rope_n) {  // interleaved pair (n, n + 1) of head dim pair (n % 64) / 2
                    // rounded to fp16 first, as a separate rotary pass over the stored q / k would see them
                    v0 = __half2float(__float2half_rn(v0)); v1 = __half2float(__float2half_rn(v1));
                    int pos = (m / p.pdiv) % p.pmod, pr = (n & 63) >> 1;
                    float c = __half2float(((const __half *)p.rc)[pos * 32 + pr]), sn = __half2float(((const __half *)p.rs)[pos * 32 + pr]);
                    float r0 = v0 * c - v1 * sn, r1 = v0 * sn + v1 * c; v0 = r0; v1 = r1;
                }
                *(u32 *)(p.C + (size_t)m * p.ldc + n) = pack2(v0, v1);
            }
        }
}

// ---------------------------------------------------------------------------------------------------------------
// Flash attention, head dim 64, 8 heads, fp32 online softmax. Token (batch b, position s) is row
// b * brow + s * srow; q, k, v, o point at head 0's column and have row strides ldq / ldo (halves).
// out = softmax(q k^T / 8) v, times gate[row * 8 + head] when gate is given. grid (ceil(S / 64), batches * 8), 128 threads.
#define FSK 72
extern "C" __global__ void __launch_bounds__(128) flash(const u16 *q, const u16 *k, const u16 *v, u16 *o, const float *gate,
                                                        int S, int brow, int srow, int ldq, int ldo) {
    __shared__ __align__(16) u16 sQ[64 * FSK], sK[64 * FSK], sV[64 * FSK];
    const int head = blockIdx.y & 7, bt = blockIdx.y >> 3, q0 = blockIdx.x * 64;
    const int tid = threadIdx.x, warp = tid >> 5, lane = tid & 31, g = lane >> 2, t = lane & 3;
    const size_t base = (size_t)bt * brow;
    auto row_of = [&](int s) { return base + (size_t)s * srow; };
    // Q tile: 64 rows x 64 d; 128 threads x 4 chunks of 8 halves
    for (int c = tid; c < 64 * 8; c += 128) {
        int r = c >> 3, d = (c & 7) * 8, s = q0 + r;
        *(uint4 *)&sQ[r * FSK + d] = s < S ? *(const uint4 *)(q + row_of(s) * ldq + head * 64 + d) : make_uint4(0, 0, 0, 0);
    }
    __syncthreads();
    u32 qa[4][4];  // 16 rows of this warp x 64 d: 4 chunks of k16
#pragma unroll
    for (int kc = 0; kc < 4; kc++)
        ldsm_x4(qa[kc][0], qa[kc][1], qa[kc][2], qa[kc][3], &sQ[(warp * 16 + (lane & 15)) * FSK + kc * 16 + (lane >> 4) * 8]);
    float oacc[8][4];
#pragma unroll
    for (int j = 0; j < 8; j++)
#pragma unroll
        for (int c = 0; c < 4; c++) oacc[j][c] = 0.f;
    float mrow[2] = {-1e30f, -1e30f}, lrow[2] = {0.f, 0.f};
    const float sc = 0.125f * 1.4426950408889634f;  // 1/sqrt(64), in log2 units
    for (int k0 = 0; k0 < S; k0 += 64) {
        __syncthreads();
        for (int c = tid; c < 64 * 8; c += 128) {
            int r = c >> 3, d = (c & 7) * 8, s = k0 + r;
            bool ok = s < S; size_t off = row_of(s) * ldq + head * 64 + d;
            *(uint4 *)&sK[r * FSK + d] = ok ? *(const uint4 *)(k + off) : make_uint4(0, 0, 0, 0);
            *(uint4 *)&sV[r * FSK + d] = ok ? *(const uint4 *)(v + off) : make_uint4(0, 0, 0, 0);
        }
        __syncthreads();
        float sacc[8][4];
#pragma unroll
        for (int j = 0; j < 8; j++)
#pragma unroll
            for (int c = 0; c < 4; c++) sacc[j][c] = 0.f;
        // S = Q K^T: n = key (8 tiles of 8), k = d (4 chunks of 16)
#pragma unroll
        for (int kc = 0; kc < 4; kc++)
#pragma unroll
            for (int jp = 0; jp < 4; jp++) {
                u32 b0, b1, b2, b3;
                ldsm_x4(b0, b1, b2, b3, &sK[(jp * 16 + (lane & 7) + ((lane >> 4) << 3)) * FSK + kc * 16 + ((lane >> 3) & 1) * 8]);
                mma1688(sacc[jp * 2], qa[kc][0], qa[kc][1], b0); mma1688(sacc[jp * 2], qa[kc][2], qa[kc][3], b1);
                mma1688(sacc[jp * 2 + 1], qa[kc][0], qa[kc][1], b2); mma1688(sacc[jp * 2 + 1], qa[kc][2], qa[kc][3], b3);
            }
        // online softmax per row (rows g and g + 8 of the warp's 16); a row's 64 scores sit in the 4 lanes of a quad
        float mx[2] = {-1e30f, -1e30f};
#pragma unroll
        for (int j = 0; j < 8; j++)
#pragma unroll
            for (int c = 0; c < 4; c++) {
                int key = k0 + j * 8 + t * 2 + (c & 1);
                float s_ = key < S ? sacc[j][c] * sc : -1e30f; sacc[j][c] = s_;
                mx[c >> 1] = fmaxf(mx[c >> 1], s_);
            }
        float alpha[2];
#pragma unroll
        for (int h = 0; h < 2; h++) {
            mx[h] = fmaxf(mx[h], __shfl_xor_sync(0xffffffff, mx[h], 1));
            mx[h] = fmaxf(mx[h], __shfl_xor_sync(0xffffffff, mx[h], 2));
            float mn = fmaxf(mrow[h], mx[h]); alpha[h] = exp2f(mrow[h] - mn); mrow[h] = mn; lrow[h] *= alpha[h];
        }
        u32 pa[8][2];  // P as A fragments: key tile j's 16 x 8 accumulator is the A operand of k step j
#pragma unroll
        for (int j = 0; j < 8; j++) {
            float p0 = exp2f(sacc[j][0] - mrow[0]), p1 = exp2f(sacc[j][1] - mrow[0]);
            float p2 = exp2f(sacc[j][2] - mrow[1]), p3 = exp2f(sacc[j][3] - mrow[1]);
            lrow[0] += p0 + p1; lrow[1] += p2 + p3;
            pa[j][0] = pack2(p0, p1); pa[j][1] = pack2(p2, p3);
        }
#pragma unroll
        for (int j = 0; j < 8; j++) { oacc[j][0] *= alpha[0]; oacc[j][1] *= alpha[0]; oacc[j][2] *= alpha[1]; oacc[j][3] *= alpha[1]; }
        // O += P V: k = key (8 steps of 8), n = d (8 tiles of 8); V row-major [key][d] read transposed
#pragma unroll
        for (int kp = 0; kp < 4; kp++)      // keys 16 kp .. 16 kp + 15
#pragma unroll
            for (int dp = 0; dp < 4; dp++) {  // d 16 dp .. 16 dp + 15
                u32 b0, b1, b2, b3;  // (keys 0-7, d 0-7), (keys 8-15, d 0-7), (keys 0-7, d 8-15), (keys 8-15, d 8-15)
                ldsm_x4_t(b0, b1, b2, b3, &sV[(kp * 16 + (lane & 7) + ((lane >> 3) & 1) * 8) * FSK + dp * 16 + (lane >> 4) * 8]);
                mma1688(oacc[dp * 2], pa[kp * 2][0], pa[kp * 2][1], b0); mma1688(oacc[dp * 2], pa[kp * 2 + 1][0], pa[kp * 2 + 1][1], b1);
                mma1688(oacc[dp * 2 + 1], pa[kp * 2][0], pa[kp * 2][1], b2); mma1688(oacc[dp * 2 + 1], pa[kp * 2 + 1][0], pa[kp * 2 + 1][1], b3);
            }
    }
#pragma unroll
    for (int h = 0; h < 2; h++) {
        lrow[h] += __shfl_xor_sync(0xffffffff, lrow[h], 1);
        lrow[h] += __shfl_xor_sync(0xffffffff, lrow[h], 2);
    }
#pragma unroll
    for (int h = 0; h < 2; h++) {
        int s = q0 + warp * 16 + g + h * 8;
        if (s >= S) continue;
        size_t r = row_of(s);
        float f = 1.f / lrow[h];
        if (gate) f *= gate[r * 8 + head];
#pragma unroll
        for (int j = 0; j < 8; j++)
            *(u32 *)(o + r * ldo + head * 64 + j * 8 + t * 2) = pack2(oacc[j][h * 2] * f, oacc[j][h * 2 + 1] * f);
    }
}

// ---------------------------------------------------------------------------------------------------------------
// RMSNorm over rows of 384: y = s / sqrt(mean(s^2) + eps) * gamma, s = x (+ r, written back to x when r is given).
// One warp per row, 12 elements per lane. In place is fine (y may be x).
extern "C" __global__ void rmsnorm(u16 *x, const u16 *r, const u16 *gamma, u16 *y, int rows, float eps) {
    int row = blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5), lane = threadIdx.x & 31;
    if (row >= rows) return;
    __half *xr = (__half *)x + (size_t)row * 384;
    float v[12], ss = 0.f;
#pragma unroll
    for (int i = 0; i < 6; i++) {
        __half2 a = ((__half2 *)xr)[lane + i * 32];
        float a0 = __low2float(a), a1 = __high2float(a);
        if (r) { __half2 b = ((const __half2 *)(r + (size_t)row * 384))[lane + i * 32]; a0 += __low2float(b); a1 += __high2float(b); }
        v[2 * i] = a0; v[2 * i + 1] = a1; ss += a0 * a0 + a1 * a1;
    }
    if (r) {
#pragma unroll
        for (int i = 0; i < 6; i++) ((__half2 *)xr)[lane + i * 32] = __floats2half2_rn(v[2 * i], v[2 * i + 1]);
    }
#pragma unroll
    for (int o = 16; o; o >>= 1) ss += __shfl_xor_sync(0xffffffff, ss, o);
    float inv = rsqrtf(ss / 384.f + eps);
    __half2 *yr = (__half2 *)((__half *)y + (size_t)row * 384);
#pragma unroll
    for (int i = 0; i < 6; i++) {
        __half2 gm = ((const __half2 *)gamma)[lane + i * 32];
        yr[lane + i * 32] = __floats2half2_rn(v[2 * i] * inv * __low2float(gm), v[2 * i + 1] * inv * __high2float(gm));
    }
}

// gates[row * 8 + h] = sigmoid(xn[row] . Wg[h] + bg[h]); Wg fp16 [8][384]. One warp per row.
extern "C" __global__ void gates(const u16 *xn, const u16 *wg, const u16 *bg, float *out, int rows) {
    int row = blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5), lane = threadIdx.x & 31;
    if (row >= rows) return;
    const __half *x = (const __half *)xn + (size_t)row * 384;
    float xv[12];
#pragma unroll
    for (int i = 0; i < 12; i++) xv[i] = __half2float(x[lane + i * 32]);
#pragma unroll
    for (int h = 0; h < 8; h++) {
        float s = 0.f;
#pragma unroll
        for (int i = 0; i < 12; i++) s += xv[i] * __half2float(((const __half *)wg)[h * 384 + lane + i * 32]);
#pragma unroll
        for (int o = 16; o; o >>= 1) s += __shfl_xor_sync(0xffffffff, s, o);
        if (lane == 0) out[(size_t)row * 8 + h] = 1.f / (1.f + expf(-(s + __half2float(((const __half *)bg)[h]))));
    }
}

// band split input: for frame t and band b, the band's features u in [edge[b], edge[b+1]) of the (T, 7916) gather
// (u = 2 j + c: spec[fi[j], t, c]), RMSNorm'd in fp32 with gamma, written fp16 to out[b][t][0 .. kpad) (zero padded).
// One warp per (t, b); grid (T, 60).
extern "C" __global__ void bandsplit(const float *spec, const int *fi, const int *edge, const float *gamma, const int *goff,
                                     u16 *out, int T, int kpad, float eps) {
    int t = blockIdx.x, b = blockIdx.y, lane = threadIdx.x;
    int e0 = edge[b], n = edge[b + 1] - e0;
    float ss = 0.f;
    for (int u = lane; u < n; u += 32) { int uu = e0 + u; float x = spec[((size_t)fi[uu >> 1] * T + t) * 2 + (uu & 1)]; ss += x * x; }
#pragma unroll
    for (int o = 16; o; o >>= 1) ss += __shfl_xor_sync(0xffffffff, ss, o);
    float inv = rsqrtf(ss / n + eps);
    __half *dst = (__half *)out + ((size_t)b * T + t) * kpad;
    for (int u = lane; u < kpad; u += 32) {
        float y = 0.f;
        if (u < n) { int uu = e0 + u; y = spec[((size_t)fi[uu >> 1] * T + t) * 2 + (uu & 1)] * inv * gamma[goff[b] + u]; }
        dst[u] = __float2half_rn(y);
    }
}

// GLU of the last mask-estimator layer: masks[t, mo[b] + j] = h[t, j] * sigmoid(h[t, nh + j]), h = h3 + ho[b] with
// row stride 2 nh (nh = nh[b]). grid (T, 60), 256 threads. masks (T, 7916) as the 32-bit floats the host hands on,
// each the 16-bit float nearest the value: what a 16-bit output read back as 32-bit would hold.
extern "C" __global__ void glu(const u16 *h3, const int *ho, const int *nhs, const int *mo, float *masks, int T) {
    int t = blockIdx.x, b = blockIdx.y, nh = nhs[b];
    const __half *h = (const __half *)h3 + ho[b] + (size_t)t * 2 * nh;
    for (int j = threadIdx.x; j < nh; j += blockDim.x) {
        float a = __half2float(h[j]), g = __half2float(h[nh + j]);
        masks[(size_t)t * 7916 + mo[b] + j] = __half2float(__float2half_rn(a / (1.f + expf(-g))));
    }
}
