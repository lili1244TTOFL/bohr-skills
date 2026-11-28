# Introduction

## Background

In recent years, cross-domain transfer has attracted widespread attention \cite{chen2019}.
Stability under domain shift remains unresolved \cite{liu2020}. Low-resource pairs are where the
published gains do not hold \cite{sun2018}.

## Related Work

We group prior work into two lines. The first line aligns domains with parallel corpora:
\cite{chen2019} reports consistent gains on four bilingual pairs, but the method relies on large
parallel corpora and degrades on low-resource pairs. The second line aligns with pseudo labels:
\cite{liu2020} trains without parallel data, however its convergence is unstable across datasets.

## Research Gap

Both lines degrade on low-resource pairs and report results on different metrics \cite{chen2019; liu2020}.

## Contributions

- We propose a cross-lingual alignment module (Section 3).
- We evaluate on three datasets (Table 2).
- We cover low-resource pairs (Fig. 4).
