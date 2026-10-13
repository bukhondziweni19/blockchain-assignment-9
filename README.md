# Financial Blockchain Capstone: African Remittances Payment Prototype

BLCH9X2 Blockchain, Assignment 9 (group). Master of Financial Engineering, University of Johannesburg.

Bukho Ndziweni (220081696), Sizwe November (200819365), Tshireletso Sethaiso (221016661), Bridget Maposa (221034491)

The group's SimpleBlockchain build from Assignments 1 to 7 (hashing, blocks, transactions, ECDSA (Elliptic Curve Digital Signature Algorithm) wallets, chain verification, mining and fork choice) is extended into a simplified remittance platform. A Sender pays in rand (ZAR) and receives a simulated stablecoin, **mZAR**, at one for one. The Sender signs a transfer of mZAR to a Recipient, the transfer is mined into a block, and the Recipient redeems the mZAR for loti (LSL), which is pegged to the rand at par. Every step goes through a REST (Representational State Transfer) API (Application Programming Interface), and every claim the platform makes can be checked against the chain by an outside party.

This is a teaching prototype. It moves no real money and must not be exposed on a network.

## Files

| File | What it is |
|---|---|
| `simple_blockchain.py` | The chain core carried forward from Assignments 1 to 7. Capstone extensions are marked `CAPSTONE` in the code |
| `stablecoin.py` | The mZAR rules: genesis charter, account table, mint, transfer and burn, chain validation, and the ledger with its pending pool |
| `remittance.py` | The payment platform: custody of keys, the reserve cash book, the on-ramp, the off-ramp and signed receipts |
| `payment_api.py` | The Flask API. One node per process, bound to `127.0.0.1` |
| `verify.py` | The independent verifier: downloads the blocks and recomputes every check locally |
| `explorer.py` | The explorer: an overview and a payment record, as text or as a web page |
| `demo.py` | The scripted demonstration: starts a node and prints every request and reply |
| `tests.py` | 26 rule tests that run without the web server |
| `sample_outputs.txt` | The output of `demo.py`, written by the program |
| `Blockchain_Group_Assignment_9.ipynb` | The same work cell by cell, with outputs |
| `requirements.txt` | The three external dependencies: Flask, requests and ecdsa |

## How the parts connect

```
   client (demo.py, notebook, curl, browser)
        |  HTTP (HyperText Transfer Protocol) requests and JSON (JavaScript Object Notation) replies
        v
   payment_api.py        one endpoint per action
        |
        v
   remittance.py         custody (key store), on-ramp, off-ramp, receipts
        |                         |
        v                         v
   stablecoin.py          ReserveBook (off chain)
   Ledger: rules,         rand in, loti out,
   pending pool, state    running balance
        |
        v
   simple_blockchain.py   Block, Transaction, Wallet, Blockchain, mining, fork choice

   verify.py and explorer.py read GET /chain and recompute everything on their own side.
```

## Running it

Python 3.10 or later.

```bash
pip install -r requirements.txt
```

**1. The scripted demonstration.** Starts one node, runs the demonstration script from the brief, and ends with `44 passed, 0 failed`. It takes a few seconds.

```bash
python demo.py            # the full demonstration, also written to sample_outputs.txt
python demo.py --hold     # the same, then keeps the node running so the explorer page can be opened
```

**2. The notebook.** Open `Blockchain_Group_Assignment_9.ipynb` and choose **Restart Kernel and Run All Cells**. Its first code cell installs Flask, requests or ecdsa into the notebook's own Python if any of them is missing.

**3. The tests.**

```bash
python tests.py           # ends with "26 passed, 0 failed"
```

**4. By hand.** Start a node in one terminal and use it from another.

```bash
python payment_api.py --seed blch9x2-capstone-demo   # one node on port 5000, with repeatable demonstration keys
python explorer.py                                   # the explorer overview, as text
python explorer.py --txid <transaction id>           # the payment record for one transaction
python verify.py --txid <transaction id>             # audit the chain and prove the transaction is in it
python verify.py --receipt RCPT-0001                 # check an off-ramp receipt against the chain
```

The explorer is also served as a web page at http://127.0.0.1:5000/explorer while a node is running.

If port 5000 is taken, `demo.py` and the notebook move to the next free port by themselves and print the address they chose. For a node started by hand, use `python payment_api.py --port 5100`.

If a command reports that Flask, requests or ecdsa is missing although the notebook runs, the terminal is using a different Python installation from the notebook. Running `python -m pip install -r requirements.txt` in that same terminal installs the libraries for it.

## The demonstration script

| Brief step | What `demo.py` does | Evidence printed |
|---|---|---|
| i. Create wallets | The Sender creates a key pair locally and registers only its address. The Recipient asks the platform to create and hold a wallet. The reserve agent and validator wallets belong to the operator | Section 1 |
| ii. On-ramp | The Sender deposits 1 000 rand. The reserve records it, the reserve agent signs a mint of 1 000 mZAR, and block 1 is mined | Section 2 |
| iii. Transfer | The Sender signs a transfer of 750 mZAR locally and submits it. Block 2 is mined | Section 3 |
| iv. Off-ramp | The Recipient redeems 750 mZAR. The burn is mined in block 3, 750 loti is paid out and a signed receipt is issued | Section 4 |
| v. Verification and explorer | The node audits its chain. An outside party then recomputes everything from the downloaded blocks, proves the transfer is in block 2, checks the receipt, and shows that an edited copy of the chain is rejected. The explorer prints the overview and the payment record | Section 5 |
| vi. Invalid attempts | Six requests are refused: an altered transaction (401), a wrong key (401), insufficient balance (422), a replay (409), a forged mint (403) and an unregistered customer (403) | Section 6 |
| Payment API | A second customer sends 400 through `POST /payments`, which runs the whole flow in one call | Section 7 |

## Design in brief

**Reserve rule.** 1 mZAR = 1 ZAR held in reserve. The rule is written into the genesis block, together with the address of the reserve agent, the only address allowed to mint. Rand is recorded in the reserve before mZAR is minted, and mZAR is burned and confirmed before loti is paid out, so the reserve never falls below the mZAR in circulation. `GET /reserve` sets the cash book against the supply replayed from the chain.

**Two assets.** mZAR is created only by a mint and destroyed only by a burn. Miners are paid in a separate native coin, SBC, which cannot be transferred or redeemed. Mining therefore never changes the mZAR supply.

**Account model.** Balances are not stored in blocks. They are rebuilt by replaying every transaction from genesis. Each address has a sequence number, and a transaction is valid only if it carries the next one, which stops a signed transaction being used twice.

**Custody.** A hosted wallet's key is held by the platform, which signs on the customer's instruction (`POST /transfer`, `POST /offramp`). A self-custody wallet's key never reaches the platform: its owner signs locally and submits the finished transaction (`POST /transactions`). The demonstration uses one of each.

**Verification.** Every transaction carries its sender's public key and signature, and the ledger checks both. `verify.py` takes only the blocks from the node and recomputes every hash, link, proof of work, reward, signature, sequence number and balance.

**Proof of work.** The demonstration mines at difficulty 4: a block hash must start with four zeros, which takes about 16^4 = 65 536 hashes on average. The difficulty is declared in the genesis block and every later block must meet it. The network minimum from Assignment 7 is 2.

**Corridor.** South Africa to Lesotho. The loti is pegged to the rand at par, so 1 mZAR = 1 ZAR = 1 LSL and no exchange rate is needed. Other corridors would need a rate and foreign-exchange liquidity at the off-ramp.

## Reproducibility

The demonstration keys are derived from fixed seeds, signatures are deterministic (RFC 6979), timestamps come from a logical clock, and every nonce search starts at zero. Every address, signature, transaction identifier, nonce and block hash is therefore identical on any machine and on every run. Only the mining `seconds` differ. A run of `python demo.py` should print these values:

| Item | Value |
|---|---|
| Genesis hash | `85c4405998fb7eab0f33418a16412e86cec9d2201ca637ac0eb215546a9aa60c` |
| Reserve agent | `0d8cb096595dcf1ed5b2ad861890b98f3cd96176` |
| Sender | `1f20731db9e34b92d4609b65ca7a029a7ebdde3f` |
| Recipient | `e2637520d3ed6a039b23ff672bfbefaabcb72174` |
| Mint of 1 000 mZAR, block 1 | `6251b477c8ad865ca220e4e727e99663647290bf8a04f3ca4e9817ea0ddd9c72` |
| Transfer of 750 mZAR, block 2 | `3c61f3cd84569501ace3677890e846d41cbcf6cab0ea47b1f9537cfbf06d590f` |
| Burn of 750 mZAR, block 3 | `ecad44c787736a5f91563538b53331b1b900fa76805a7361eee5f58d8ae517ad` |
| Block 1 hash (nonce 63 361) | `0000d7e464bf3761ee07a0750b742dda484148277bb7c65f582782c2a4cff40c` |
| Block 2 hash (nonce 14 622) | `00000bf91e0edf80f73a2869df04bb388eec46c6046958cea036dc0491c4cf9c` |
| Block 3 hash (nonce 55 685) | `0000ff511afc83cea88fe4df8755e5c9aa1f48ac0f519d90d2f858a6f2c11d7d` |

Seeded keys are for demonstrations only, because anyone who knows the seed can rebuild the private keys. Started without `--seed`, a node uses random keys, and `--wall-clock` stamps records with the real time.

## API reference

Every request and reply is JSON, except the two explorer pages. Amounts are whole numbers. Addresses are 40 hexadecimal characters.

### Wallets and custody

| Endpoint | Body | Success | Refusals |
|---|---|---|---|
| `POST /wallets` | `{"name": "Recipient"}` for a hosted wallet, or `{"name": "Sender", "address": "<address>"}` for self-custody | 201, the wallet's public record | 400 bad name or address, 409 name or address already registered |
| `GET /wallets` | | 200, every wallet's public record. Private keys are never returned | |
| `GET /accounts/<address>` | | 200, `confirmed`, `after_pending`, `next_sequence`, `mining_rewards` | 400 not an address |

### The payment flow

| Endpoint | Body | Success | Refusals |
|---|---|---|---|
| `POST /onramp` | `{"address": "<address>", "amount": 1000, "bank_reference": "EFT-..."}` | 201, the conversion record and the mint's `txid` | 400 bad amount, 403 address not a registered customer |
| `POST /transfer` | `{"sender": "<hosted address>", "recipient": "<address>", "amount": 750}` | 201, the `txid` and the signed transaction | 403 the platform does not hold that key, 422 insufficient balance |
| `POST /transactions` | A complete signed transaction (see below) | 201, the `txid` | 400 malformed, 401 bad signature or wrong key, 403 not authorised, 409 replay or wrong sequence, 422 insufficient balance |
| `POST /offramp` | `{"address": "<hosted address>", "amount": 750}` | 202, the burn's `txid`. The payout follows when the burn is mined | 403 the platform does not hold that key, 422 insufficient balance |
| `POST /mine` | none | 201, the block, `attempts`, `seconds` and `receipts_issued` | |
| `POST /payments` | `{"sender": "<hosted address>", "recipient": "<hosted address>", "amount": 400, "redeem": true}` | 201, the payment record with the `txid`, block index and block hash of each step | 400, 403 as above |
| `GET /payments/<id>` | | 200, the payment record | 404 |

### Records

| Endpoint | Success |
|---|---|
| `GET /reserve` | 200, the cash book, the supply replayed from the chain, `fully_backed` and `surplus` |
| `GET /receipts` | 200, every on-ramp conversion record and off-ramp receipt |
| `GET /receipts/<id>` | 200, one record, found by its identifier (`DEP-0001`, `RCPT-0001`) or by its transaction identifier. 404 if there is none |

### Verification and explorer

| Endpoint | Success |
|---|---|
| `GET /chain` | 200, every block, with the difficulty, genesis hash and reserve agent |
| `GET /chain/verify` | 200, the node's own audit: `valid`, the first `problem` if any, and one line per block |
| `GET /transactions/pending` | 200, the transactions waiting to be mined |
| `GET /transactions/<txid>` | 200, `status`, `block_index`, `block_hash`, `position_in_block`, `confirmations` and the transaction. 404 if unknown |
| `GET /explorer` | 200, the overview as a web page |
| `GET /explorer/tx/<txid>` | 200, the payment record for one transaction as a web page |

### A signed transaction

This is the Sender's transfer from the demonstration, as submitted to `POST /transactions`. The first five fields are the Assignment 3 schema. The last four are capstone extensions, and all of them except `signature` are covered by the signature.

```json
{
  "sender": "1f20731db9e34b92d4609b65ca7a029a7ebdde3f",
  "recipient": "e2637520d3ed6a039b23ff672bfbefaabcb72174",
  "amount": 750,
  "timestamp": 1790000150,
  "signature": "30450220767059fd5bfd6f6d3753bf2d7b4a32098d86200f29170a64e0cdd58f5bbef7d2022100afb273b5c2e2d091db83e621511706c716c2edb747b48abb1a5c0a05cadace52",
  "kind": "transfer",
  "asset": "mZAR",
  "sequence": 0,
  "public_key": "016b3da12aca62fe6f421048212bc4697c6a2096c89d752813891c4166beede4bcbef27855f53d6c8a6e77b12d6eb9ca01a3d1f5ee7ba0cfb9f9e7fdfeaff87f"
}
```

`kind` is `mint`, `transfer` or `burn`. A mint must be signed by the reserve agent, and a burn must name the reserve agent as `recipient`. `sequence` is the number returned as `next_sequence` by `GET /accounts/<address>`. A transaction may also carry a `reference` of up to 64 characters.

### A receipt

`GET /receipts/RCPT-0001` after the demonstration:

```json
{
  "id": "RCPT-0001",
  "type": "off-ramp",
  "customer": "e2637520d3ed6a039b23ff672bfbefaabcb72174",
  "customer_name": "Recipient",
  "burned": 750,
  "asset": "mZAR",
  "paid_out": 750,
  "payout_currency": "LSL",
  "rate": "1 mZAR = 1 ZAR = 1 LSL",
  "status": "paid",
  "txid": "ecad44c787736a5f91563538b53331b1b900fa76805a7361eee5f58d8ae517ad",
  "block_index": 3,
  "block_hash": "0000ff511afc83cea88fe4df8755e5c9aa1f48ac0f519d90d2f858a6f2c11d7d",
  "issued_at": 1790000360,
  "issuer": "0d8cb096595dcf1ed5b2ad861890b98f3cd96176",
  "issuer_public_key": "297e4fa470f5fad9c28052b6ff133611f44ba259dac46484bfeb5ec4456a8cc7a595374514ff333531551d44c3543582dadfd9b27f374efb05cde26ca07d5b5c",
  "signature": "304402202da0eb4e2950caebcf9f98ec210684d40fb95645ff0c392fd36f6ad8c780e557022006a7f6aec4674d056b5804d6bee7548656b7881e2bbf2cbac17c198b5622cefc"
}
```

The reserve agent's signature covers every field above it. `verify.py --receipt RCPT-0001` checks the signature against the address in the genesis block and confirms that the burn it names is in the block it names.

### One payment by hand, with curl

With a node running (`python payment_api.py --seed blch9x2-capstone-demo`), two hosted wallets and one call are enough:

```bash
curl -s -X POST http://127.0.0.1:5000/wallets -H "Content-Type: application/json" -d '{"name": "Sender"}'
curl -s -X POST http://127.0.0.1:5000/wallets -H "Content-Type: application/json" -d '{"name": "Recipient"}'
curl -s -X POST http://127.0.0.1:5000/payments -H "Content-Type: application/json" \
     -d '{"sender": "<Sender address>", "recipient": "<Recipient address>", "amount": 750}'
curl -s http://127.0.0.1:5000/chain/verify
```

## Safety and simplifications

- The server binds to `127.0.0.1` and has no login. Hosted keys are held in memory and are lost when the node stops.
- The reserve is a cash book inside the program, not a bank account, and deposits and payouts are simulated.
- One node mines every block. The fork-choice rule from Assignment 7 is carried forward and tested (`tests.py`), but the demonstration does not run several nodes.
- There are no transaction fees, and the know-your-customer check is a registration step only.

The written report discusses these limitations and the financial design in full.
