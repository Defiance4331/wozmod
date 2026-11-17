/* Included first in every PocketSphinx file (build.rs): its calls to the C maths library's transcendental functions
 * go to the libm crate (speech.rs), which gives the same bits on every platform, where Linux's and Windows' own
 * functions differ in their last bits. sqrt, floor, ceil and fabs are exact everywhere and stay as they are. */
#ifndef WOZMOD_PS_MATH_H
#define WOZMOD_PS_MATH_H
#include <math.h>

#define WOZMOD_MATH_1(name) double wozmod_ps_##name(double); float wozmod_ps_##name##f(float);
WOZMOD_MATH_1(log) WOZMOD_MATH_1(log10) WOZMOD_MATH_1(log2) WOZMOD_MATH_1(log1p) WOZMOD_MATH_1(exp)
WOZMOD_MATH_1(exp2) WOZMOD_MATH_1(expm1) WOZMOD_MATH_1(cos) WOZMOD_MATH_1(sin) WOZMOD_MATH_1(tan)
WOZMOD_MATH_1(atan) WOZMOD_MATH_1(acos) WOZMOD_MATH_1(asin) WOZMOD_MATH_1(cosh) WOZMOD_MATH_1(sinh)
WOZMOD_MATH_1(tanh)
double wozmod_ps_pow(double, double); float wozmod_ps_powf(float, float);
double wozmod_ps_atan2(double, double); float wozmod_ps_atan2f(float, float);
#undef WOZMOD_MATH_1

#define log(x) wozmod_ps_log(x)
#define logf(x) wozmod_ps_logf(x)
#define log10(x) wozmod_ps_log10(x)
#define log10f(x) wozmod_ps_log10f(x)
#define log2(x) wozmod_ps_log2(x)
#define log2f(x) wozmod_ps_log2f(x)
#define log1p(x) wozmod_ps_log1p(x)
#define log1pf(x) wozmod_ps_log1pf(x)
#define exp(x) wozmod_ps_exp(x)
#define expf(x) wozmod_ps_expf(x)
#define exp2(x) wozmod_ps_exp2(x)
#define exp2f(x) wozmod_ps_exp2f(x)
#define expm1(x) wozmod_ps_expm1(x)
#define expm1f(x) wozmod_ps_expm1f(x)
#define cos(x) wozmod_ps_cos(x)
#define cosf(x) wozmod_ps_cosf(x)
#define sin(x) wozmod_ps_sin(x)
#define sinf(x) wozmod_ps_sinf(x)
#define tan(x) wozmod_ps_tan(x)
#define tanf(x) wozmod_ps_tanf(x)
#define atan(x) wozmod_ps_atan(x)
#define atanf(x) wozmod_ps_atanf(x)
#define acos(x) wozmod_ps_acos(x)
#define acosf(x) wozmod_ps_acosf(x)
#define asin(x) wozmod_ps_asin(x)
#define asinf(x) wozmod_ps_asinf(x)
#define cosh(x) wozmod_ps_cosh(x)
#define coshf(x) wozmod_ps_coshf(x)
#define sinh(x) wozmod_ps_sinh(x)
#define sinhf(x) wozmod_ps_sinhf(x)
#define tanh(x) wozmod_ps_tanh(x)
#define tanhf(x) wozmod_ps_tanhf(x)
#define pow(x, y) wozmod_ps_pow(x, y)
#define powf(x, y) wozmod_ps_powf(x, y)
#define atan2(x, y) wozmod_ps_atan2(x, y)
#define atan2f(x, y) wozmod_ps_atan2f(x, y)
#endif
