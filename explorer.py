"""
explorer.py
Assignment 9: Financial Blockchain Capstone (the explorer)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS PROGRAM DOES
    Shows what a node holds in a form a person can read, the way a public block
    explorer does. There are two views, each produced as plain text and as an HTML
    (HyperText Markup Language) page:

        the overview         a summary of the node, its blocks, its transactions, the
                             balance of every wallet, the conversions and the reserve
        the payment record   one transaction with its full identifier, its signature
                             check, and the block that holds it

    Balances are not copied from the node. The explorer replays the chain itself
    (verify.py), so what it shows is what any outside party would work out.

    Run from the command line, this program is a client of the API (Application
    Programming Interface) in payment_api.py and reads only its public endpoints.

HOW TO RUN (a node must already be running, see payment_api.py or "python demo.py --hold")
    python explorer.py                                 the overview of the node on port 5000
    python explorer.py --txid <transaction id>         the payment record for one transaction
    python explorer.py --last 10                       show up to 10 rows per table
    python explorer.py --html explorer.html            also save the view as a web page
    python explorer.py --node http://127.0.0.1:5001    read a different node
"""

import argparse                              # argparse reads options such as --txid from the command line
import html                                  # html.escape makes text safe to place inside a web page
from datetime import datetime, timezone      # used to turn a Unix timestamp into a readable date and time

from simple_blockchain import BLOCK_REWARD, COINBASE_SENDER, tx_id   # the reward size, the reward's sender name and transaction identifiers
from verify import DEFAULT_NODE, audit_chain, call_node, locate      # the node reader and the local checks

# ===========================================================================
# CONFIGURATION
# ===========================================================================

DEFAULT_ROWS = 8                # how many recent blocks and transactions the overview lists
HASH_CHARACTERS = 16            # how much of each 64 character hash the overview displays
ADDRESS_CHARACTERS = 10         # how much of an unnamed address the overview displays

# ===========================================================================
# READING A NODE THROUGH ITS API
# ===========================================================================

def fetch_snapshot(node: str = DEFAULT_NODE):
    """Read a node over HTTP (HyperText Transfer Protocol). Return a snapshot, or None if it cannot be reached."""
    replies = {}                                                            # the decoded reply of each endpoint
    for path in ("/chain", "/transactions/pending", "/wallets", "/reserve", "/receipts"):   # the five public endpoints the explorer needs
        status, reply = call_node("GET", node, path)                        # read one endpoint
        if status != 200:                                                   # every one of them must answer
            return None                                                     # otherwise there is nothing reliable to show
        replies[path] = reply                                               # keep the reply
    chain = replies["/chain"]                                               # the chain reply also carries the network's parameters
    return {                                                                # the same shape that RemittancePlatform.snapshot() produces
        "node": node,                                                       # which node was read
        "asset": chain["asset"],                                            # the stablecoin's name
        "native_asset": chain["native_asset"],                              # the mining-reward coin's name
        "difficulty": chain["difficulty"],                                  # the mining rule
        "genesis_hash": chain["genesis_hash"],                              # the fingerprint that identifies the network
        "reserve_agent": chain["reserve_agent"],                            # who may mint
        "chain": chain["chain"],                                            # the blocks
        "pending": replies["/transactions/pending"]["transactions"],        # the pending pool
        "wallets": replies["/wallets"]["wallets"],                          # the known wallets
        "reserve": replies["/reserve"],                                     # the proof-of-reserves report
        "conversions": replies["/receipts"]["conversions"],                 # the on-ramp records
        "receipts": replies["/receipts"]["receipts"],                       # the off-ramp receipts
    }

# ===========================================================================
# SMALL FORMATTING HELPERS
# ===========================================================================

def shorten(hash_hex: str) -> str:
    """Show the start of a hash followed by three dots, so tables stay narrow."""
    return hash_hex[:HASH_CHARACTERS] + "..."       # the leading characters are enough to recognise a hash


def format_time(timestamp) -> str:
    """Turn a Unix timestamp into a date and time in UTC (Coordinated Universal Time)."""
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)     # interpret the number of seconds as a UTC moment
    return moment.strftime("%Y-%m-%d %H:%M")                        # for example "2026-09-21 21:34"


def wallet_names(snapshot: dict) -> dict:
    """Return a lookup from address to the name it was registered under."""
    names = {record["address"]: record["name"] for record in snapshot["wallets"]}   # every registered wallet
    names[COINBASE_SENDER] = "(new coins)"                          # a reward has no sender, so say where the value came from
    return names                                                    # the finished lookup


def party(address: str, names: dict) -> str:
    """Show an address by its registered name, or by its first characters if it has none."""
    return names.get(address) or address[:ADDRESS_CHARACTERS] + "..."   # a name when there is one, otherwise a shortened address


def describe(tx: dict, snapshot: dict) -> tuple:
    """Return (kind, amount with its unit) for any record found in a block."""
    if tx.get("kind") == "charter":                                         # the genesis record
        return "charter", "-"                                               # it moves no value
    if tx["sender"] == COINBASE_SENDER:                                     # the miner's reward
        return "reward", f"{tx['amount']:,} {snapshot['native_asset']}"     # paid in the native coin
    return tx["kind"], f"{tx['amount']:,} {tx['asset']}"                    # a stablecoin transaction, paid in mZAR

# ===========================================================================
# TURNING A SNAPSHOT INTO ROWS
# ===========================================================================

def transaction_row(place: str, tx: dict, snapshot: dict, names: dict) -> list:
    """Describe one record as a list of table cells."""
    kind, amount = describe(tx, snapshot)                           # what the record does and how much it moves
    txid = tx_id(tx)                                                # its full identifier
    sequence = tx.get("sequence")                                   # only signed stablecoin transactions carry a sequence number
    return [                                                        # one cell per column
        place,                                                      # the block number, or the word "pending"
        kind,                                                       # charter, reward, mint, transfer or burn
        "(genesis)" if kind == "charter" else party(tx["sender"], names),   # who it is from; the charter comes from the genesis rules
        party(tx["recipient"], names),                              # who it is to
        amount,                                                     # the amount with its unit
        "-" if sequence is None else str(sequence),                 # the sender's sequence number, if any
        tx.get("reference") or "-",                                 # the reference, if any
        (shorten(txid), f"/explorer/tx/{txid}"),                    # the start of the identifier, linked to its payment record on the web page
    ]


def build_overview(snapshot: dict, last: int = DEFAULT_ROWS) -> tuple:
    """Reduce a snapshot to the summary facts and the tables that both views display."""
    chain, reserve, names = snapshot["chain"], snapshot["reserve"], wallet_names(snapshot)   # the three things most rows are built from
    asset, native = snapshot["asset"], snapshot["native_asset"]                 # the two asset names
    audit = audit_chain(chain, snapshot["genesis_hash"])                        # replay the chain here, without trusting the node's own figures
    verdict = ("valid: every hash, link, proof of work, signature and balance was recomputed by this explorer"   # the chain passed
               if audit["valid"] else f"INVALID: {audit['problem']}")           # or the first broken rule
    backed = "yes" if reserve["fully_backed"] else "NO"                         # the reserve rule in one word
    facts = [                                                                   # the summary shown at the top
        ("Node", snapshot["node"]),                                             # which node this is
        ("Asset", f"{asset}. {reserve['rule']}"),                               # the stablecoin and its reserve rule
        ("Reserve agent", snapshot["reserve_agent"]),                           # the only address that may mint
        ("Genesis hash", snapshot["genesis_hash"]),                             # the fingerprint that identifies this network
        ("Chain height", f"{len(chain) - 1} ({len(chain)} blocks including genesis)"),   # height counts from zero
        ("Difficulty", f"{snapshot['difficulty']} leading zeros"),              # the mining rule
        ("Chain check", verdict),                                               # the explorer's own verdict
        ("Supply", f"{reserve['supply']:,} {asset} in circulation ({reserve['minted']:,} minted, {reserve['burned']:,} burned)"),   # from the chain
        ("Reserve", f"{reserve['reserve_balance']:,} {reserve['reserve_currency']} held. Fully backed: {backed}"),   # from the cash book
        ("Pending", f"{len(snapshot['pending'])} transaction(s) waiting to be mined"),   # the size of the pool
    ]
    block_rows = [                                                              # one row per recent block
        [str(block["index"]), format_time(block["timestamp"]), str(len(block["transactions"])),   # position, time and number of records
         f"{block['nonce']:,}", shorten(block["hash"]), shorten(block["previous_hash"])]         # the nonce found, and the two hashes
        for block in reversed(chain[-last:])                                    # the last few blocks, newest first
    ]
    mined = [(block["index"], tx) for block in chain for tx in block["transactions"]]   # every mined record with its block number
    transaction_rows = [transaction_row(str(index), tx, snapshot, names) for index, tx in reversed(mined[-last:])]   # the last few, newest first
    pending_rows = [transaction_row("pending", tx, snapshot, names) for tx in snapshot["pending"]]   # one row per waiting transaction
    balances, rewards = {}, {}                                                  # worked out below from the explorer's own replay
    if audit["valid"]:                                                          # balances from an invalid chain would mean nothing
        for index, tx in mined:                                                 # walk every mined record once more
            kind, _ = describe(tx, snapshot)                                    # what the record does
            if kind == "reward":                                                # a reward credits the miner in the native coin
                rewards[tx["recipient"]] = rewards.get(tx["recipient"], 0) + BLOCK_REWARD   # add the reward
            if kind in ("mint", "transfer"):                                    # a mint or transfer credits the recipient
                balances[tx["recipient"]] = balances.get(tx["recipient"], 0) + tx["amount"]   # add the amount
            if kind in ("transfer", "burn"):                                    # a transfer or burn debits the sender
                balances[tx["sender"]] = balances.get(tx["sender"], 0) - tx["amount"]         # subtract the amount
    custody = {record["address"]: record["custody"] for record in snapshot["wallets"]}   # who holds each registered wallet's key
    holders = list(custody) + [address for address in balances if address not in custody]   # registered wallets first, then any other holder
    balance_rows = [[names.get(address, "(unregistered)"), address, custody.get(address, "-"),   # name, full address and custody
                     f"{balances.get(address, 0):,}", f"{rewards.get(address, 0):,}"]            # mZAR held and SBC earned
                    for address in holders]                                     # one row per holder
    conversion_rows = [                                                         # on-ramp records, then off-ramp receipts
        [record["id"], record["type"], record["customer_name"] or party(record["customer"], names),   # identifier, direction and customer
         f"{record['deposited']:,} {record['deposit_currency']}", f"{record['minted']:,} {record['asset']}",   # rand in, mZAR out
         record["status"], "-" if record["block_index"] is None else str(record["block_index"]),              # status and block
         (shorten(record["txid"]), f"/explorer/tx/{record['txid']}")]           # the mint transaction, linked
        for record in snapshot["conversions"]                                   # one row per deposit
    ] + [                                                                       # followed by
        [record["id"], record["type"], record["customer_name"] or party(record["customer"], names),   # identifier, direction and customer
         f"{record['burned']:,} {record['asset']}", f"{record['paid_out']:,} {record['payout_currency']}",   # mZAR in, loti out
         record["status"], str(record["block_index"]),                          # status and block
         (shorten(record["txid"]), f"/explorer/tx/{record['txid']}")]           # the burn transaction, linked
        for record in snapshot["receipts"]                                      # one row per redemption
    ]
    reserve_rows = [[str(entry["entry"]), entry["type"], f"{entry['amount']:+,}", party(entry["customer"], names),   # number, type, signed amount, customer
                     entry["reference"], f"{entry['balance']:,}"]               # the record it belongs to and the running balance
                    for entry in reserve["entries"]]                            # one row per cash-book entry
    transaction_headers = ["Block", "Kind", "From", "To", "Amount", "Seq", "Reference", "Txid"]   # shared by two tables
    sections = [                                                                # each table: a title, column headings and rows
        (f"BLOCKS (newest first, {len(block_rows)} of {len(chain)})",           # the title says how many are shown
         ["Index", "Time (UTC)", "Records", "Nonce", "Hash", "Previous hash"], block_rows),   # headings, then the rows
        (f"TRANSACTIONS (newest first, {len(transaction_rows)} of {len(mined)})", transaction_headers, transaction_rows),   # the mined records
        (f"PENDING ({len(pending_rows)})", transaction_headers, pending_rows),  # the waiting transactions
        (f"WALLET BALANCES (confirmed, replayed from the chain)", ["Name", "Address", "Custody", asset, native], balance_rows),   # who holds what
        ("CONVERSIONS AND RECEIPTS", ["Id", "Type", "Customer", "In", "Out", "Status", "Block", "Txid"], conversion_rows),   # the two desks
        (f"RESERVE CASH BOOK ({reserve['reserve_currency']})", ["Entry", "Type", "Amount", "Customer", "Reference", "Balance"], reserve_rows),   # the money behind the coin
    ]
    return facts, sections                                                      # everything the two views need


def build_payment_record(snapshot: dict, txid: str):
    """Return the facts about one transaction and its block, or None if the chain does not hold it."""
    found = locate(snapshot["chain"], txid)                                     # search the chain, recomputing identifiers
    if found is None:                                                           # the transaction is not in any block
        return None                                                             # there is nothing to show
    tx, names = found["transaction"], wallet_names(snapshot)                    # the transaction and the name lookup
    block = snapshot["chain"][found["block_index"]]                             # the block that holds it
    kind, amount = describe(tx, snapshot)                                       # what it does and how much it moves
    signature = {True: "valid (checked against the public key carried in the transaction)",   # the signature verdict in words
                 False: "INVALID", None: "none (this record is not signed)"}[found["signature_valid"]]   # for all three cases
    sealed = "matches" if found["block_hash"] == found["block_hash_recomputed"] else "DOES NOT MATCH"   # the block hash verdict in words
    return [                                                                    # one (label, value) pair per line
        ("Transaction ID", txid),                                               # the full 64 character identifier
        ("Kind", kind),                                                         # charter, reward, mint, transfer or burn
        ("Amount", amount),                                                     # the amount with its unit
        ("From", "(genesis)" if kind == "charter" else f"{party(tx['sender'], names)}  {tx['sender']}"),   # the sender's name and full address
        ("To", f"{party(tx['recipient'], names)}  {tx['recipient']}"),          # the recipient's name and full address
        ("Sequence", "-" if tx.get("sequence") is None else str(tx["sequence"])),   # the sender's sequence number
        ("Reference", tx.get("reference") or "-"),                              # the link to an off-chain record
        ("Created (UTC)", format_time(tx["timestamp"])),                        # when the transaction was created
        ("Signature", signature),                                               # rechecked by the explorer
        ("Status", f"confirmed, {found['confirmations']} confirmation(s)"),     # how deep it is in the chain
        ("Block", f"{found['block_index']} (position {found['position_in_block']} of {len(block['transactions'])} records; position 0 is the miner's reward)"),   # where it sits
        ("Block hash", found["block_hash"]),                                    # the full hash of its block
        ("Block hash check", f"recomputed from the block's contents: {sealed}"),   # the explorer's own recomputation
        ("Previous block hash", block["previous_hash"]),                        # the link to the block before
        ("Block nonce", f"{block['nonce']:,}"),                                 # the nonce the miner found
        ("Block difficulty", f"{block.get('difficulty', '-')} leading zeros"),  # the difficulty the block declares
        ("Block time (UTC)", format_time(block["timestamp"])),                  # when the block was mined
    ]

# ===========================================================================
# THE TEXT VIEWS
# ===========================================================================

def cell_text(cell) -> str:
    """Return the text of a table cell. A linked cell is a (text, link) pair, and only its text is printed."""
    return cell[0] if isinstance(cell, tuple) else cell     # drop the link, which only the web page uses


def render_text(snapshot: dict, last: int = DEFAULT_ROWS) -> str:
    """Return the overview as plain text for the terminal."""
    facts, sections = build_overview(snapshot, last)                            # the facts and tables to display
    lines = ["=" * 110, f"EXPLORER  node {snapshot['node']}", "=" * 110]        # a heading between two rules
    for name, value in facts:                                                   # the summary, one fact per line
        lines.append(f"{name:<14}: {value}")                                    # "<14" pads the name so the colons line up
    for title, headers, rows in sections:                                       # then each table in turn
        lines.append("")                                                        # a blank line before the table
        lines.append(title)                                                     # the table's title
        if not rows:                                                            # an empty table is shown as one word
            lines.append("  (none)")                                            # so the reader knows nothing is missing
            continue                                                            # move on to the next table
        table = [headers] + [[cell_text(cell) for cell in row] for row in rows] # the headings followed by the rows, as plain text
        widths = [max(len(row[column]) for row in table) for column in range(len(headers))]   # each column is as wide as its longest cell
        for row in table:                                                       # print the headings, then every row
            lines.append("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())   # pad each cell to its column width
    return "\n".join(lines)                                                     # one string with a line break between lines


def render_payment_text(snapshot: dict, txid: str) -> str:
    """Return the payment record for one transaction as plain text."""
    record = build_payment_record(snapshot, txid)                               # the facts to display
    if record is None:                                                          # the chain does not hold the transaction
        return f"PAYMENT RECORD\n  No confirmed transaction has the identifier {txid}"   # say so plainly
    lines = ["PAYMENT RECORD"]                                                  # the heading
    for label, value in record:                                                 # one fact per line
        lines.append(f"  {label:<20}: {value}")                                 # "<20" pads the label so the colons line up
    return "\n".join(lines)                                                     # one string with a line break between lines

# ===========================================================================
# THE WEB PAGE VIEWS
# ===========================================================================

# The styling rules for the pages, written in CSS (Cascading Style Sheets). The pages are black on white only,
# and every rule starts with .bx so the styling cannot leak into a surrounding page such as a notebook.
# "!important" keeps the tables looking the same when a notebook applies its own table styling.
PAGE_STYLE = """
.bx { font-family: Calibri, Arial, sans-serif; color: #000; background: #fff; max-width: 1150px; margin: 0 auto; padding: 16px; }
.bx h1 { font-size: 22px; margin: 0 0 12px 0; }
.bx h2 { font-size: 15px; margin: 22px 0 6px 0; }
.bx table { border-collapse: collapse; width: 100%; font-size: 13px; table-layout: auto !important; margin: 0 !important; }
.bx th, .bx td { border: 1px solid #000 !important; padding: 4px 8px; text-align: left !important; color: #000; background: #fff; }
.bx th { font-weight: bold; }
.bx td.mono { font-family: Consolas, "Courier New", monospace; overflow-wrap: anywhere; }
.bx a { color: #000; }
.bx p { font-size: 13px; margin: 4px 0; }
"""
MONOSPACE_COLUMNS = ("Hash", "Previous hash", "Txid", "Address", "Value")   # columns that hold hashes or addresses use a fixed-width font


def html_table(headers: list, rows: list) -> str:
    """Return one HTML table. Every cell is escaped, so text from a transaction can never act as page code."""
    if not rows:                                                                # an empty table is shown as one word
        return "<p>(none)</p>"                                                  # so the reader knows nothing is missing
    head = "".join(f"<th>{html.escape(cell)}</th>" for cell in headers)         # the heading cells
    body = ""                                                                   # the rows are added to this text one by one
    for row in rows:                                                            # each row of the table
        cells = ""                                                              # the cells of this row
        for header, cell in zip(headers, row):                                  # each cell together with its column heading
            style = ' class="mono"' if header in MONOSPACE_COLUMNS else ""      # hashes and addresses use a fixed-width font
            if isinstance(cell, tuple):                                         # a linked cell: (text, link)
                content = f'<a href="{html.escape(cell[1])}">{html.escape(cell[0])}</a>'   # the escaped text inside an escaped link
            else:                                                               # an ordinary cell
                content = html.escape(cell)                                     # the escaped text
            cells += f"<td{style}>{content}</td>"                               # add the cell
        body += f"<tr>{cells}</tr>"                                             # close the row
    return f"<table><tr>{head}</tr>{body}</table>"                              # the finished table


def html_page(title: str, parts: list) -> str:
    """Wrap pieces of content in a complete, self-contained web page."""
    content = "\n".join(parts)                                                  # all the pieces, one per line
    return (                                                                    # the complete page
        "<!DOCTYPE html>\n"                                                     # tells the browser this is modern HTML
        '<html lang="en"><head><meta charset="utf-8">\n'                        # the language and the character set
        f"<title>{html.escape(title)}</title>\n"                                # the text shown on the browser tab
        f"<style>{PAGE_STYLE}</style></head>\n"                                 # the styling rules above
        f'<body><div class="bx">\n{content}\n</div></body></html>\n'            # the visible content
    )


def render_html(snapshot: dict, last: int = DEFAULT_ROWS) -> str:
    """Return the overview as a self-contained web page."""
    facts, sections = build_overview(snapshot, last)                            # the same facts and tables as the text view
    parts = [f"<h1>{html.escape(snapshot['asset'])} explorer, node {html.escape(snapshot['node'])}</h1>"]   # the page heading
    parts.append(html_table(["Item", "Value"], [[name, value] for name, value in facts]))   # the summary as a two column table
    for title, headers, rows in sections:                                       # then each table in turn
        parts.append(f"<h2>{html.escape(title)}</h2>")                          # the table's title
        parts.append(html_table(headers, rows))                                 # the table itself
    return html_page(f"{snapshot['asset']} explorer", parts)                    # wrap the pieces in a page


def render_payment_html(snapshot: dict, txid: str) -> str:
    """Return the payment record for one transaction as a self-contained web page."""
    record = build_payment_record(snapshot, txid)                               # the facts to display
    parts = ["<h1>Payment record</h1>"]                                         # the page heading
    if record is None:                                                          # the chain does not hold the transaction
        parts.append(f"<p>No confirmed transaction has the identifier {html.escape(txid)}</p>")   # say so plainly
    else:                                                                       # the transaction was found
        parts.append(html_table(["Item", "Value"], [[label, value] for label, value in record]))   # the facts as a two column table
    parts.append('<p><a href="/explorer">Back to the overview</a></p>')         # a link back to the first page
    return html_page("Payment record", parts)                                   # wrap the pieces in a page

# ===========================================================================
# RUNNING THE EXPLORER FROM THE COMMAND LINE
# ===========================================================================

def main():
    """Read a running node and print the overview or one payment record."""
    parser = argparse.ArgumentParser(description="Print an explorer view of a running node.")   # describes the program for --help
    parser.add_argument("--node", default=DEFAULT_NODE, help="address of the node to read (default http://127.0.0.1:5000)")   # which node
    parser.add_argument("--txid", help="show the payment record for this transaction identifier")   # optional
    parser.add_argument("--last", type=int, default=DEFAULT_ROWS, help="rows per table in the overview (default 8)")   # how much to show
    parser.add_argument("--html", metavar="FILE", help="also save the view as a web page in FILE")   # optional page output
    arguments = parser.parse_args()                                             # read what was typed
    snapshot = fetch_snapshot(arguments.node)                                   # read the node through its API
    if snapshot is None:                                                        # the node did not answer
        raise SystemExit(f"No node answered at {arguments.node}. Start one with: python payment_api.py")   # explain how to fix it
    rows = max(1, arguments.last)                                               # always show at least one row
    if arguments.txid:                                                          # one transaction was asked for
        text, page = render_payment_text(snapshot, arguments.txid), render_payment_html(snapshot, arguments.txid)   # the payment record, both ways
    else:                                                                       # otherwise show everything
        text, page = render_text(snapshot, rows), render_html(snapshot, rows)   # the overview, both ways
    print(text)                                                                 # the text view
    if arguments.html:                                                          # a page was asked for as well
        with open(arguments.html, "w", encoding="utf-8") as page_file:          # open the file for writing
            page_file.write(page)                                               # write the page
        print(f"\nWeb page written to {arguments.html}")                        # say where it went


if __name__ == "__main__":      # True only when this file is run directly, not when it is imported
    main()                      # print the explorer
