# Security

- Do not attach real offering memoranda, rent rolls, T12s, or ops databases to issues.
- `resident_name` and resident balances must never appear in fixtures or traces.
- Secrets (Graph tokens, PMS keys, `.env`) stay out of git.
- Report vulnerabilities privately to the CODEOWNERS contacts once the public org exists.

The default product is local-first. A rented model is optional and must not receive raw PII.


## Reporting

Report suspected vulnerabilities privately to the maintainer
(`security@` address to be published with the repository). Do not open
public issues for exploitable findings. Include the exit code, the typed
error, and (if possible) a synthetic reproducer — never live deal data.
