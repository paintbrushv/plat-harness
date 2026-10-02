# Synthetic income review acceptance case

Run `plat-harness review-income --case samples/deals/synthetic_income_quality/case.json`.
The fixture is wholly fabricated and contains no resident rows, seller files, or
deal names. It exercises the income evidence needed for the acquisition loop.

The command checks twelve monthly values for net rent, tax, and every
other-income line and ties those values to the reported T12 net rent, other
income, tax, and NOI. It reports how many months two rolling T12s share, monthly
fee recurrence, the current-resident move-in signal, paired expenses retained
in operating costs, and the explicit house credit for each GL category. It
keeps negative bad debt in full. T12 net rent already includes its booked
vacancy, concessions, and rental writeoffs; the base adds only a concession
floor shortfall. The rent-roll snapshot is a separate stress and therefore
receives its own writeoff and concession treatment.

The fabricated case expects $1,011,400 trailing operating NOI, $877,880 house
base NOI, and $746,880 current-roll spot NOI. Its tax line includes a negative
month. The provisional tax assumption and absent cash collection evidence keep
`pricing_certified` false and the command exits 2. It computes no offer price, CoC, IRR, or
tax reset. A future underwriting adapter can consume this structured bridge;
it must preserve these warnings and call the Decimal engine for returns.

Input contract: `schema_version` must be `income-review/1.0.0` and
`data_class` must be `synthetic`; monetary amounts are decimal
strings or integers; every monthly series has twelve entries; booked income
lines identify `evidence: booked_gl`; `paired_expense` is already inside T12
operating expense. A `bad_debt` line with a negative annual balance must have
credit rate 1. A verified post-sale tax status requires a source reference.
The input also identifies when a paired expense is included in T12 operating
expense. Without that evidence the review refuses to call it retained. These
checks stop incomplete or contradictory cases instead of filling gaps with
zeros.
