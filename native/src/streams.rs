//! The optimal alignments of two stage scripts' command streams (script_alignment._alignment): the score table and
//! its earliest and latest trace. Python builds the rows and the events' likeness, and reads the traced pairs.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// The pairs of rows (our row, their row) of two optimal alignments of the two streams, each from the end backwards:
/// the earliest (where alignments tie, each pair as early in the streams as it can go) and the latest.
///
/// `ours` is per row (token, base gain, event), `theirs` per row (token, event); `likeness[e][f]` is what our event
/// e and their event f add to a pair of rows whose tokens agree. A pair scores its row's base gain plus likeness,
/// any other pair of rows nothing.
#[pyfunction]
pub fn stream_alignments(
    py: Python<'_>,
    ours: Vec<(usize, i64, usize)>,
    theirs: Vec<(usize, usize)>,
    likeness: Vec<Vec<i64>>,
) -> PyResult<(Vec<(usize, usize)>, Vec<(usize, usize)>)> {
    let width = likeness.first().map_or(0, Vec::len);
    if likeness.iter().any(|row| row.len() != width)
        || ours.iter().any(|&(_, _, e)| e >= likeness.len())
        || (!likeness.is_empty() && theirs.iter().any(|&(_, f)| f >= width))
    {
        return Err(PyValueError::new_err(
            "stream_alignments: an event is outside the likeness table, or its rows differ in length",
        ));
    }
    Ok(py.detach(|| {
        let table = Table::fill(&ours, &theirs, &likeness);
        (table.traced(true), table.traced(false))
    }))
}

struct Table<'a> {
    ours: &'a [(usize, i64, usize)],
    theirs: &'a [(usize, usize)],
    likeness: &'a [Vec<i64>],
    /// score[i * (m + 1) + j]: the best score of our first i rows against their first j.
    score: Vec<i64>,
}

impl<'a> Table<'a> {
    fn fill(
        ours: &'a [(usize, i64, usize)],
        theirs: &'a [(usize, usize)],
        likeness: &'a [Vec<i64>],
    ) -> Self {
        let tokens = ours
            .iter()
            .map(|r| r.0)
            .chain(theirs.iter().map(|r| r.0))
            .max()
            .map_or(0, |t| t + 1);
        let mut places = vec![Vec::new(); tokens];
        for (j, &(token, _)) in theirs.iter().enumerate() {
            places[token].push(j);
        }
        let (n, m) = (ours.len(), theirs.len());
        let mut score = vec![0i64; (n + 1) * (m + 1)];
        for (i, &(token, base, e)) in ours.iter().enumerate() {
            // Rows rise along j, so a place where the tokens differ adds nothing to the row above: start from it,
            // raise each place j + 1 a pair at j reaches, then carry each maximum on along the row.
            let (done, rest) = score.split_at_mut((i + 1) * (m + 1));
            let above = &done[i * (m + 1)..];
            let row = &mut rest[..m + 1];
            row.copy_from_slice(above);
            for &j in &places[token] {
                let value = above[j] + base + likeness[e][theirs[j].1];
                if value > row[j + 1] {
                    row[j + 1] = value;
                }
            }
            for j in 1..=m {
                row[j] = row[j].max(row[j - 1]);
            }
        }
        Table {
            ours,
            theirs,
            likeness,
            score,
        }
    }

    /// What pairing our row i with their row j scores: 0 unless their tokens agree.
    fn gain(&self, i: usize, j: usize) -> i64 {
        let (token, base, e) = self.ours[i];
        if self.theirs[j].0 != token {
            return 0;
        }
        base + self.likeness[e][self.theirs[j].1]
    }

    fn at(&self, i: usize, j: usize) -> i64 {
        self.score[i * (self.theirs.len() + 1) + j]
    }

    /// Walk back from the end: a pair where the score came from it, else up, else left; `earliest` prefers up or
    /// left to a pair whenever they score the same.
    fn traced(&self, earliest: bool) -> Vec<(usize, usize)> {
        let (mut found, mut i, mut j) = (Vec::new(), self.ours.len(), self.theirs.len());
        while i > 0 && j > 0 {
            let g = self.gain(i - 1, j - 1);
            let here = self.at(i, j);
            let pair = g > 0 && here == self.at(i - 1, j - 1) + g;
            let (up, left) = (here == self.at(i - 1, j), here == self.at(i, j - 1));
            if pair && !(earliest && (up || left)) {
                found.push((i - 1, j - 1));
                i -= 1;
                j -= 1;
            } else if up {
                i -= 1;
            } else {
                j -= 1;
            }
        }
        found
    }
}
