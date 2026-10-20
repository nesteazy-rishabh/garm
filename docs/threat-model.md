# Threat model

## System

A back-office agent runs as scheduled jobs over a company's system of record (`README.md`, Environment). Jobs share
only the database. Staff run a daily callback process that verifies high-scrutiny vendor changes by phone.

## Assets

- Money leaving the company: vendor payments and customer refunds.
- Who the company talks to: vendor and customer contact details, especially remittance and bank fields.
- Data the company holds: bank details, payment history and customer records.

## Trust boundaries

| Source | Trusted? | Notes |
|---|---|---|
| Seed data (the record as of the start) | yes | Stands for long-established records. |
| Staff writes and staff verifications | yes | Staff follow the process exactly. The callback goes to whoever owns the number dialled. |
| The handbook and job instructions | yes | Fixed and known to the attacker (Kerckhoffs). |
| Inbox: email bodies, PDF text, web forms | **no** | Any outsider can write here, including from lookalike domains and compromised vendor mailboxes. |
| Agent writes | derived | As trustworthy as their inputs. Provenance (D5) tracks this. |

## Attacker

- **Can:** send messages through every inbound channel; register lookalike domains; control a real vendor's mailbox
  (family X); own a phone number and answer calls to it; know the handbook and which defence is deployed.
- **Cannot:** read or write the database directly; impersonate staff; see the agent's prompts or outputs (except
  through the adaptive attacker's stage feedback); control the vendor's original phone line; act as an insider.
- **Goals:** redirect a payment, get a fake invoice paid, exfiltrate data, obtain an unauthorised refund, or swap a
  contact address.

## Attack timing

- **Same-run:** the injected message is acted on in the job that reads it.
- **Cross-run:** attacker data is written into the record in one job and acted on in a later one, which reads only
  the record.
- **Laundered (family X):** the attacker first makes a change that policy allows (the phone number), then requests a
  high-scrutiny change. The staff callback reaches the attacker, who confirms it. A genuine staff verification now
  exists, and every later check passes.

## Out of scope

- Insiders and compromised staff accounts.
- Control of the vendor's original phone line (SIM swap, compromised phone system).
- Long-dwell attacks that make the first change more than D6's 14-day cooling-off window before the second. Every
  episode lasts days.
- Attacks on the model itself (weights, fine-tuning, the provider) and on the harness.
- Denial of service and cost exhaustion.

## What each defence assumes

| | Assumes |
|---|---|
| D1 | The model follows prompt rules over injected text. |
| D2, D3 | A human or model approver reads each card and can judge it from the card. |
| D4 | The record and the handbook are correct, so checking proposals against them is enough. Laundering breaks this. |
| D5 | Where a value came from is enough for an approver to judge it. The honest twin breaks this. |
| D6 | The number on file before any recent change still reaches the real counterparty. |
