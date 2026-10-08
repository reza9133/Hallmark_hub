# Hallmark

An on-chain quality-certification system for GenLayer, built as a **hub** plus
any number of **satellite** contracts.

- **`hallmark_hub.py` (HallmarkHub)** holds one written standard (criteria text,
  pass mark, version) and a public registry of certified subjects.
- **`assay_dossier.py` (AssayDossier)** is the satellite. One is deployed per
  audited subject, using the hub's address as a constructor argument. The
  subject's owner attaches web evidence, and the dossier asks the validators'
  LLMs to score that evidence against the hub's current standard.

The hub never receives pushed messages. It **pulls** verdicts from dossiers
with read-only calls, so the system does not depend on asynchronous
cross-contract messages.

```
                 reads standard                    reads link + verdict
   AssayDossier ----------------> HallmarkHub <----------------------------+
   (one per subject)   get_standard()   |  apply / approve / attest        |
        |                               |                                  |
        +-- add_evidence, audit()       +-- registry: PENDING -> APPROVED  |
            LLM scores web evidence         tier: GOLD / CERTIFIED /       |
                                            REJECTED / NONE ---------------+
```

## Files

| File | Role |
| --- | --- |
| `hallmark_hub.py` | Hub: standard, registry, approval, attestation |
| `assay_dossier.py` | Satellite: evidence and LLM audit (deploy once per subject) |
| `tests/` | pytest suite that runs both contracts outside the VM (see [Tests](#tests)) |
| `pytest.ini`, `requirements-dev.txt` | Test configuration and the one test dependency |

Both files declare the same runner pin in their first line. Keep them
identical: cross-contract calls between contracts built on different runners
are a known source of failures.

## Deploy on Studionet

Deploy the hub first, then every satellite with the hub's address.

1. **Deploy `hallmark_hub.py`** with:

   | Argument | Example |
   | --- | --- |
   | `registry_name` | `Open Sourcing Registry` |
   | `standard_title` | `Responsible Sourcing Standard v1` |
   | `standard_criteria` | see below |
   | `pass_mark` | `70` |

   Example `standard_criteria`:

   > Score out of 100. (1) A publicly documented supplier list or sourcing
   > policy: 25 points. (2) An independent third-party certification or audit
   > report: 30 points. (3) Published batch-level traceability or origin
   > information: 25 points. (4) A public corrective-action or complaint
   > process: 20 points.

2. Copy the hub's address exactly as the explorer shows it (`0x` followed by 40
   hex characters).
3. **Deploy `assay_dossier.py`** with `hub_address` (from step 2), `subject`
   (up to 80 characters) and `description` (up to 600 characters). Deploy as
   many dossiers as you like, one per subject.

## Typical flow

1. Dossier owner: `add_evidence(url, note)` one to five times.
2. Dossier owner: `audit()`. Wait until the transaction finalizes, then check
   `get_verdict()`.
3. Dossier owner: `hub.apply(dossier_address)`. Call it from the same account
   that deployed the dossier; the hub checks this.
4. Hub owner: `hub.approve(dossier_address)`.
5. Anyone: `hub.attest(dossier_address)` copies the verdict into the registry
   and assigns a tier.
6. Hub owner: `update_standard(...)` bumps the version. Existing results show
   `is_current: false`, and a dossier must run `audit()` again and be attested
   again.

Tiers: `GOLD` when the score is at least `max(90, pass_mark)`, `CERTIFIED` when
it is at least `pass_mark`, otherwise `REJECTED`. Revoked dossiers show `NONE`.

If `pass_mark` is 90 or higher, the two thresholds coincide: every passing score
is `GOLD` and `CERTIFIED` never occurs. Use a pass mark below 90 if you want both
tiers to be reachable.

**Always read `effective_tier`, not `tier`.** Registry views return both. When
the standard changes, `effective_tier` becomes `STALE` until the dossier is
audited and attested again, while the stored `tier` keeps the old value.

**Wait for finality before attesting.** The hub reads the dossier's latest
state, which can still be non-final. Call `attest` only after the dossier's
`audit()` transaction has finalized, so a later appeal cannot leave the registry
with a result that was overturned.

## Method reference

### HallmarkHub

| Method | Who | Purpose |
| --- | --- | --- |
| `update_standard(title, criteria, pass_mark)` | owner | Replace the standard and bump its version |
| `approve(dossier)` | owner | Approve a pending dossier, or reinstate a revoked one |
| `revoke(dossier)` | owner | Revoke a dossier and clear its tier, score and version |
| `propose_owner(new_owner)` | owner | Start a two-step ownership transfer |
| `accept_ownership()` | proposed owner | Complete the transfer |
| `cancel_ownership_proposal()` | owner | Cancel a pending transfer |
| `apply(dossier)` | dossier owner | Register a dossier as pending |
| `attest(dossier)` | anyone | Pull the dossier's latest verdict into the registry |
| `get_standard()` / `get_summary()` | view | Standard details / registry overview (JSON) |
| `get_dossier(dossier)` | view | One registry record, with `is_current` and `effective_tier` |
| `list_dossiers(offset, limit)` | view | Paged records (same fields), at most 50 per page |

### AssayDossier

| Method | Who | Purpose |
| --- | --- | --- |
| `add_evidence(url, note)` | owner | Attach a public https page, at most 5 items |
| `clear_evidence()` | owner | Remove all evidence |
| `audit()` | owner | Read the hub's standard and have the LLM score the evidence |
| `get_link()` / `get_verdict()` | view | Read by the hub |
| `get_evidence()` / `get_audited_evidence()` | view | Current evidence / the evidence the last verdict was based on |
| `get_info()` | view | Dossier details |

All views return JSON strings.

## Design notes

- **Consensus.** The audit uses a custom leader/validator check: validators
  re-run the audit and accept the leader's score if it is within 15 points and
  on the same side of the pass mark. This tolerates small LLM and web-page
  differences without accepting a different outcome.
- **Untrusted evidence.** The prompt tells the model to treat page content,
  owner notes, the subject and its description as data and to ignore
  instructions inside them.
- **Storage safety.** Storage is only written outside the non-deterministic
  block, and the hub and dossier never write to each other.
- **Why approval exists.** Anyone can deploy a contract that returns a perfect
  score from `get_verdict()`. The hub therefore only attests dossiers its owner
  has approved, so approval is where the owner checks that a dossier runs
  genuine `assay_dossier.py` code.
- **Freshness.** A verdict is only accepted if it was issued under the hub's
  current standard version, and each audit can be attested only once.

## Security review

Issues found in review and fixed:

- **Server-side request forgery.** Validators fetch evidence pages from their
  own network, so an owner could have pointed them at internal addresses and
  had a page excerpt echoed into the stored summary. `add_evidence` now accepts
  only plain public `https://` URLs on port 443: no IP addresses, no
  single-label or internal hostnames, no credentials in the URL, no non-ASCII
  characters, and no reserved top-level domains.
- **Stale tiers.** A tier earned under an old standard stayed visible after
  the standard changed. Views now return `effective_tier`, which is `STALE`
  in that case.
- **One-step ownership transfer.** A typo could lock the hub forever. Transfer
  is now two steps: `propose_owner`, then `accept_ownership` by the new owner.
- **Permanent revocation.** A mistaken `revoke` could not be undone. `approve`
  now reinstates a revoked dossier (it still needs a fresh audit and attestation).
- **Prompt injection.** The audit prompt now wraps each excerpt in explicit
  markers and tells the model to lower the score when evidence tries to
  instruct it or claims a score.
- **Unclear audit basis.** `get_audited_evidence()` now shows exactly which
  evidence the last verdict was based on.
- **Forged prompt markers.** A page, owner note, subject or description could
  print its own `<<<EVIDENCE n END>>>` line, or fake lines such as `PASS MARK:`
  or `Scoring rules:`, and make the text after it look like it came from the
  auditor. Angle brackets in pages, notes, URLs, the subject and the description
  are now replaced before they enter the prompt, and line breaks in notes, the
  subject and the description are collapsed.
- **Stale result after reinstatement.** `revoke` cleared only the tier, so a
  reinstated dossier showed `is_current: true` next to its old score. `revoke`
  now clears the score and version too. `audit_count` is kept so the revoked
  audit cannot be attested again.
- **Internal hostnames.** The blocked top-level domain list now also covers
  `localdomain`, `private` and `example`.
- **Wildcard-DNS hostnames.** Public services such as `nip.io` and `sslip.io`
  turn a name like `10.0.0.1.nip.io` into a private address, which a plain
  hostname check cannot see. `add_evidence` now also refuses these domains and
  all their subdomains (`nip.io`, `sslip.io`, `xip.io`, `localtest.me`,
  `lvh.me`, `vcap.me`, `traefik.me`). The list is best-effort: it covers
  well-known services only.
- **Confusing attest error after reinstatement.** Attesting the old audit of a
  revoked and reinstated dossier used to say the audit "has already been
  attested". It now says the audit was cleared by the revocation and that
  `audit()` must be run again.
- **Half-updated standard.** `update_standard` wrote the title and criteria
  before it validated the pass mark. On the network a failed call is reverted,
  so this could not be exploited, but it relied on the revert. It now
  validates all three arguments first and writes afterwards.

Residual risks to be aware of:

- **Prompt injection is reduced, not eliminated.** A convincing page can still
  mislead the model. Read the evidence and summary before approving a dossier.
- **Redirects and DNS tricks.** The contract cannot see where a public domain
  resolves or redirects. The blocklist of wildcard-DNS services is best-effort,
  and an attacker can register their own domain that points at a private
  address. Rely on the validators' network egress filtering for that.
- **Re-rolling audits.** An owner can run `audit()` repeatedly (changing
  evidence or hoping for a luckier score). `audit_count` is public so
  observers can see how many attempts were made.
- **Fake satellites.** Anyone can deploy a contract that returns a perfect
  score. The hub only attests dossiers its owner approved, so approval is where
  the owner must check that the dossier runs genuine `assay_dossier.py` code.
- **Registry spam.** Anyone who owns a dossier can `apply`. Pending entries are
  untrusted; frontends should filter by `status`.
- **Rendering.** `summary` and `subject` are free text. Render them as plain
  text, never as HTML.
- **No recovery for dossiers.** A dossier's owner and hub address are fixed at
  deployment. If either is wrong or the key is lost, deploy a new dossier.

## Tests

The tests need only Python and pytest. No GenVM is required.

```
pip install -r requirements-dev.txt
pytest
```

They load `hallmark_hub.py` and `assay_dossier.py` unchanged and run them
against a small in-memory stand-in for the GenLayer SDK
(`tests/genlayer_stub.py`). The stand-in uses the real EIP-55 checksum form for
addresses, rejects out-of-range `u256` values, lets cross-contract calls reach
only `@gl.public.view` methods, fails when a view changes storage, and treats a
validator that returns `False` as a failed consensus.

| File | Covers |
| --- | --- |
| `test_harness.py` | The stand-in itself: keccak and checksum vectors, view rules, calldata rules |
| `test_url_validation.py` | Valid and hostile URLs, blocked domains and top-level domains, length limits, a seeded fuzz test |
| `test_helpers.py` | Tier boundaries, stale and revoked status fields, address and text validation |
| `test_hub.py` | Standard, ownership transfer, apply, approve, revoke, attest (including malformed and hostile satellites), paging |
| `test_dossier.py` | Constructor, evidence limits, audit storage, parsing of model answers, prompt hygiene, validator agreement |
| `test_lifecycle.py` | End-to-end stories that use only public methods |

The stand-in does not model real consensus, real LLM output, real web pages or
gas, and it does not roll back storage after a failed call. The last point is
deliberate: it makes the tests stricter, because a contract must validate
before it writes. Passing tests therefore do not replace a run on Studionet.

## Status

- Both contracts pass `genvm-lint check` (lint plus schema validation), and the
  APIs used were checked against the GenLayer SDK source.
- The pytest suite (see [Tests](#tests)) passes. It covers the URL validator,
  the tier and address helpers, and the full hub and dossier lifecycle (apply,
  approve, attest, standard update, revoke and reinstate, ownership transfer)
  with a stubbed LLM and stubbed web pages. As a check on the tests
  themselves, 33 deliberate breakages of the contracts were tried and 32 made
  the suite fail. The one survivor removes a redundant `@` check whose cases
  are already rejected by the host and port checks.
- The contracts have not been run end to end on Studionet, and the LLM scoring
  and cross-contract calls are untested. Test the full flow there before
  relying on it.
- Cross-contract view calls (`apply`, `attest`, `audit`) depend on the network
  supporting them. If a call fails with a dispatch error, redeploy both
  contracts with the same runner pin in the first line.
