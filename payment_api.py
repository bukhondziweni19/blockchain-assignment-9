"""
payment_api.py
Assignment 9: Financial Blockchain Capstone (the payment API)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS PROGRAM DOES
    Wraps one RemittancePlatform (see remittance.py) in a REST (Representational State
    Transfer) API (Application Programming Interface) built with Flask. Each endpoint
    is a web address that reads or changes the platform:

        Wallets and custody
            POST /wallets                register a customer wallet (hosted, or self-custody by address)
            GET  /wallets                every known wallet
            GET  /accounts/<address>     the mZAR balance and next sequence number of an address
        The payment flow
            POST /onramp                 deposit rand, mint the same number of mZAR
            POST /transfer               send mZAR from a hosted wallet (the platform signs)
            POST /transactions           submit a transaction signed outside the platform
            POST /offramp                burn mZAR from a hosted wallet, to be paid out in loti
            POST /mine                   mine the pending transactions into a block
            POST /payments               the whole flow in one call: on-ramp, transfer, off-ramp
            GET  /payments/<id>          the record of one such payment
        Records
            GET  /reserve                the reserve cash book against the mZAR in circulation
            GET  /receipts               every conversion record and receipt
            GET  /receipts/<id>          one conversion record or receipt
        Verification
            GET  /chain                  the full chain
            GET  /chain/verify           the node's own audit of its chain
            GET  /transactions/pending   transactions waiting to be mined
            GET  /transactions/<txid>    one transaction, its block and its confirmations
        Explorer
            GET  /explorer               a web page summarising the node
            GET  /explorer/tx/<txid>     a web page for one transaction and its block

    Every reply except the two explorer pages is JSON (JavaScript Object Notation).
    The exact request and reply of every endpoint is documented in README.md.

SAFETY
    The server binds to 127.0.0.1, so it can only be reached from this computer. There
    is no login, and the platform holds private keys in memory, which is acceptable for
    a local demonstration only. Do not change the host to expose it on a network.

HOW TO RUN
    python payment_api.py                       one node on port 5000, with random keys
    python payment_api.py --seed demo           repeatable keys, for demonstrations only
    python payment_api.py --port 5001           listen on another port
    python payment_api.py --difficulty 3        mine with three leading zeros instead of four
    python payment_api.py --wall-clock          stamp records with the real time of day
"""

import argparse                      # argparse reads options such as --port from the command line
import logging                       # logging lets us quieten the web server's request log when running in the background
import socket                        # socket lets us test whether a port is already taken
import threading                     # threading lets a node run in the background while other code carries on

from flask import Flask, Response, jsonify, request   # the pieces of Flask this file uses
from werkzeug.serving import make_server              # builds a web server object that can be stopped again

from explorer import render_html, render_payment_html                       # the two explorer pages
from remittance import PlatformError, RemittancePlatform, WallClock         # the platform, its error type and the real-time clock
from simple_blockchain import MIN_DIFFICULTY                                # the lowest difficulty the network allows
from stablecoin import ASSET, DEMO_DIFFICULTY, NATIVE_ASSET, RESERVE_RULE, is_address   # names, the default difficulty and the address check
from verify import audit_chain                                              # the full chain audit

# ===========================================================================
# CONFIGURATION
# ===========================================================================

HOST = "127.0.0.1"       # the loopback address: reachable from this computer only
DEFAULT_PORT = 5000      # the port a node listens on unless told otherwise

ENDPOINTS = [                                                                           # the list returned by GET /
    "POST /wallets                register a customer wallet",                          # wallets and custody
    "GET  /wallets                every known wallet",                                  # wallets and custody
    "GET  /accounts/<address>     balance and next sequence number of an address",      # wallets and custody
    "POST /onramp                 deposit rand, mint the same number of mZAR",          # the payment flow
    "POST /transfer               send mZAR from a hosted wallet",                      # the payment flow
    "POST /transactions           submit a transaction signed outside the platform",    # the payment flow
    "POST /offramp                burn mZAR from a hosted wallet, to be paid out",      # the payment flow
    "POST /mine                   mine the pending transactions into a block",          # the payment flow
    "POST /payments               the whole flow in one call",                          # the payment flow
    "GET  /payments/<id>          the record of one such payment",                      # the payment flow
    "GET  /reserve                the reserve against the mZAR in circulation",         # records
    "GET  /receipts               every conversion record and receipt",                 # records
    "GET  /receipts/<id>          one conversion record or receipt",                    # records
    "GET  /chain                  the full chain",                                      # verification
    "GET  /chain/verify           the node's own audit of its chain",                   # verification
    "GET  /transactions/pending   transactions waiting to be mined",                    # verification
    "GET  /transactions/<txid>    one transaction, its block and its confirmations",    # verification
    "GET  /explorer               web page summarising the node",                       # explorer
    "GET  /explorer/tx/<txid>     web page for one transaction and its block",          # explorer
]

# ===========================================================================
# BUILDING THE FLASK APPLICATION
# ===========================================================================

def create_app(platform: RemittancePlatform, port: int = DEFAULT_PORT) -> Flask:
    """Create the Flask application that exposes one platform."""
    app = Flask(__name__)                          # the Flask application object
    app.config["PLATFORM"] = platform              # keep a reference so other code can reach the platform
    app.json.sort_keys = False                     # keep reply fields in the order written below
    node_url = f"http://{HOST}:{port}"             # how this node describes itself

    def body() -> dict:
        """Return the JSON object sent with the request, or refuse the request if there is none."""
        data = request.get_json(silent=True)                                    # read the JSON body, or None if it is missing or malformed
        if not isinstance(data, dict):                                          # every POST endpoint expects named fields
            raise PlatformError(400, "the request body must be a JSON object")  # 400 means "bad request"
        return data                                                             # the fields that were sent

    def explorer_view() -> dict:
        """Return a snapshot of the platform, labelled with this node's address."""
        return {"node": node_url, **platform.snapshot()}                        # the explorer shows which node it read

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------
    @app.route("/", methods=["GET"])               # GET / answers with a short status report
    def index():
        """Describe this node and list its endpoints."""
        view = platform.snapshot()                               # one consistent reading of the platform
        return jsonify({                                         # reply in JSON
            "service": f"{ASSET} remittance prototype",          # what this node is
            "node": node_url,                                    # which node is answering
            "reserve_rule": RESERVE_RULE,                        # the rule the stablecoin is issued under
            "reserve_agent": view["reserve_agent"],              # the only address that may mint
            "genesis_hash": view["genesis_hash"],                # the fingerprint that identifies this network
            "difficulty": view["difficulty"],                    # the mining rule
            "chain_length": len(view["chain"]),                  # how many blocks it holds, including genesis
            "pending_count": len(view["pending"]),               # how many transactions are waiting
            "endpoints": ENDPOINTS,                              # what can be asked of it
        }), 200                                                  # 200 means "OK"

    # -----------------------------------------------------------------------
    # Wallets and custody
    # -----------------------------------------------------------------------
    @app.route("/wallets", methods=["POST"])       # POST /wallets registers a customer
    def create_wallet():
        """Register a wallet. Without an address the platform creates and holds the key."""
        data = body()                                                            # the fields that were sent
        record = platform.register_wallet(data.get("name"), data.get("address")) # create or register the wallet
        holder = "the platform holds the key" if record["custody"] == "hosted" else "the customer holds the key"   # who can sign for it
        return jsonify({"message": f"wallet registered: {holder}", "wallet": record}), 201   # 201 means "created"

    @app.route("/wallets", methods=["GET"])        # GET /wallets lists every known wallet
    def list_wallets():
        """Return the public record of every wallet. Private keys are never included."""
        wallets = platform.snapshot()["wallets"]                                 # the public records
        return jsonify({"count": len(wallets), "wallets": wallets}), 200         # how many, then the records

    @app.route("/accounts/<address>", methods=["GET"])   # GET /accounts/<address> reports one account
    def get_account(address):
        """Return what an address holds and the sequence number it must use next."""
        if not is_address(address):                                              # refuse text that could never be an address
            raise PlatformError(400, "an address is 40 hexadecimal characters")  # 400 means "bad request"
        with platform.lock:                                                      # read the ledger while nothing is changing it
            report = platform.ledger.account(address)                            # confirmed balance, balance after pending, next sequence
            known = platform.wallets.get(address)                                # the registered record, if there is one
        report["name"] = known["name"] if known else None                        # the registered name
        report["custody"] = known["custody"] if known else None                  # who holds the key
        return jsonify(report), 200                                              # the account report

    # -----------------------------------------------------------------------
    # The payment flow
    # -----------------------------------------------------------------------
    @app.route("/onramp", methods=["POST"])        # POST /onramp converts rand into mZAR
    def onramp():
        """Record a rand deposit in the reserve and mint the same number of mZAR to the customer."""
        data = body()                                                            # the fields that were sent
        conversion = platform.on_ramp(data.get("address"), data.get("amount"), data.get("bank_reference"))   # deposit, then mint
        return jsonify({                                                         # the conversion event
            "message": f"deposit recorded in the reserve; a mint of {conversion['minted']:,} {ASSET} is waiting to be mined",   # what happened
            "conversion": conversion,                                            # the conversion record
            "reserve_balance": platform.reserve_report()["reserve_balance"],     # the reserve after the deposit
        }), 201                                                                  # 201 means "created"

    @app.route("/transfer", methods=["POST"])      # POST /transfer sends mZAR from a hosted wallet
    def transfer():
        """Sign a transfer with a key the platform holds, and place it in the pending pool."""
        data = body()                                                            # the fields that were sent
        result = platform.transfer(data.get("sender"), data.get("recipient"), data.get("amount"), data.get("reference"))   # sign and submit
        return jsonify({"message": "transfer signed by the platform and accepted into the pending pool", **result}), 201   # 201 means "created"

    @app.route("/transactions", methods=["POST"])  # POST /transactions accepts a transaction signed elsewhere
    def submit_transaction():
        """Check a transaction signed outside the platform and place it in the pending pool."""
        result = platform.submit_signed(body())                                  # the ledger applies every rule
        return jsonify({"message": "signed transaction accepted into the pending pool", **result}), 201   # 201 means "created"

    @app.route("/offramp", methods=["POST"])       # POST /offramp starts a redemption
    def offramp():
        """Burn mZAR from a hosted wallet. The payout follows when the burn is confirmed."""
        data = body()                                                            # the fields that were sent
        result = platform.off_ramp(data.get("address"), data.get("amount"))      # sign and submit the burn
        return jsonify({"message": "burn accepted into the pending pool", **result}), 202   # 202 means "accepted, not yet completed"

    @app.route("/mine", methods=["POST"])          # POST /mine mines one block
    def mine():
        """Mine a block holding every pending transaction, then settle what it made final."""
        result = platform.mine()                                                 # the proof-of-work search happens here
        return jsonify({                                                         # the block was mined
            "message": f"block {result['block']['index']} mined",                # a sentence saying so
            "block": result["block"],                                            # the new block
            "attempts": result["attempts"],                                      # how many hashes the search needed
            "seconds": round(result["seconds"], 3),                              # how long it took on this machine
            "receipts_issued": result["receipts_issued"],                        # payouts triggered by this block
        }), 201                                                                  # 201 means "created"

    @app.route("/payments", methods=["POST"])      # POST /payments runs the whole flow
    def create_payment():
        """Run on-ramp, transfer and off-ramp between two hosted wallets, mining after each step."""
        data = body()                                                            # the fields that were sent
        redeem = data.get("redeem", True)                                        # redeeming for the recipient is the default
        if not isinstance(redeem, bool):                                         # it must be true or false
            raise PlatformError(400, "'redeem' must be true or false")           # 400 means "bad request"
        payment = platform.pay(data.get("sender"), data.get("recipient"), data.get("amount"), redeem, data.get("bank_reference"))   # the whole flow
        return jsonify({"message": f"payment {payment['id']} completed", "payment": payment}), 201   # 201 means "created"

    @app.route("/payments/<payment_id>", methods=["GET"])   # GET /payments/<id> returns one payment
    def get_payment(payment_id):
        """Return the record of a payment made through POST /payments."""
        with platform.lock:                                                      # read while nothing is changing
            matches = [dict(payment) for payment in platform.payments if payment["id"] == payment_id]   # search by identifier
        if not matches:                                                          # no payment has that identifier
            raise PlatformError(404, "no payment with that identifier")          # 404 means "not found"
        return jsonify(matches[0]), 200                                          # the payment record

    # -----------------------------------------------------------------------
    # Records
    # -----------------------------------------------------------------------
    @app.route("/reserve", methods=["GET"])        # GET /reserve reports on the reserve
    def get_reserve():
        """Return the reserve cash book set against the mZAR in circulation."""
        return jsonify(platform.reserve_report()), 200                           # the proof-of-reserves report

    @app.route("/receipts", methods=["GET"])       # GET /receipts lists every record
    def list_receipts():
        """Return every on-ramp conversion record and every off-ramp receipt."""
        view = platform.snapshot()                                               # one consistent reading
        return jsonify({"conversions": view["conversions"], "receipts": view["receipts"]}), 200   # the two lists

    @app.route("/receipts/<record_id>", methods=["GET"])   # GET /receipts/<id> returns one record
    def get_receipt(record_id):
        """Return one conversion record or receipt, found by its identifier or its transaction identifier."""
        record = platform.find_record(record_id)                                 # search both lists
        if record is None:                                                       # nothing matched
            raise PlatformError(404, "no conversion record or receipt with that identifier")   # 404 means "not found"
        return jsonify(record), 200                                              # the record

    # -----------------------------------------------------------------------
    # Verification
    # -----------------------------------------------------------------------
    @app.route("/chain", methods=["GET"])          # GET /chain returns every block
    def get_chain():
        """Return the full chain with the parameters needed to check it."""
        view = platform.snapshot()                               # one consistent reading
        return jsonify({                                         # reply in JSON
            "asset": ASSET,                                      # the stablecoin
            "native_asset": NATIVE_ASSET,                        # the mining-reward coin
            "difficulty": view["difficulty"],                    # the number of leading zeros every hash must show
            "genesis_hash": view["genesis_hash"],                # the fingerprint that identifies this network
            "reserve_agent": view["reserve_agent"],              # the only address that may mint
            "length": len(view["chain"]),                        # how many blocks, including genesis
            "chain": view["chain"],                              # the blocks, oldest first
        }), 200                                                  # 200 means "OK"

    @app.route("/chain/verify", methods=["GET"])   # GET /chain/verify audits the chain
    def verify_chain():
        """Replay the whole chain and report whether every rule holds."""
        view = platform.snapshot()                                               # one consistent reading
        return jsonify(audit_chain(view["chain"], view["genesis_hash"])), 200    # the same audit an outside verifier runs

    @app.route("/transactions/pending", methods=["GET"])   # GET /transactions/pending lists the pool
    def pending_transactions():
        """Return the transactions waiting to be mined."""
        pending = platform.snapshot()["pending"]                                 # a copy of the pool
        return jsonify({"count": len(pending), "transactions": pending}), 200    # how many, then the transactions

    @app.route("/transactions/<txid>", methods=["GET"])    # GET /transactions/<txid> looks one transaction up
    def get_transaction(txid):
        """Return one transaction with its block, its position and its confirmations."""
        with platform.lock:                                                      # read the ledger while nothing is changing it
            found = platform.ledger.find_transaction(txid)                       # search the chain, then the pool
        if found is None:                                                        # this node has never seen that identifier
            raise PlatformError(404, "no transaction with that identifier is known to this node")   # 404 means "not found"
        return jsonify(found), 200                                               # where the transaction is

    # -----------------------------------------------------------------------
    # Explorer pages
    # -----------------------------------------------------------------------
    @app.route("/explorer", methods=["GET"])       # GET /explorer returns a web page instead of JSON
    def explorer_page():
        """Return the overview as an HTML (HyperText Markup Language) page."""
        return Response(render_html(explorer_view()), mimetype="text/html")      # label the reply as a web page

    @app.route("/explorer/tx/<txid>", methods=["GET"])   # GET /explorer/tx/<txid> returns one payment record
    def explorer_payment_page(txid):
        """Return the payment record of one transaction as an HTML page."""
        return Response(render_payment_html(explorer_view(), txid), mimetype="text/html")   # label the reply as a web page

    # -----------------------------------------------------------------------
    # Errors
    # -----------------------------------------------------------------------
    @app.errorhandler(PlatformError)               # runs whenever the platform refuses a request
    def refused(error):
        """Answer a refused request with the platform's reason and status code."""
        return jsonify({"error": error.message}), error.status                   # the reason, with the status the platform chose

    @app.errorhandler(404)                         # runs when no endpoint matches the web address
    def not_found(_error):
        """Answer unknown addresses in JSON, like every other reply."""
        return jsonify({"error": "no such endpoint; GET / lists the endpoints"}), 404   # 404 means "not found"

    @app.errorhandler(405)                         # runs when the address exists but the method is wrong
    def wrong_method(_error):
        """Answer a wrong method (for example GET /mine) in JSON, like every other reply."""
        return jsonify({"error": "this endpoint does not accept that method; GET / lists the endpoints"}), 405   # 405 means "method not allowed"

    return app                                     # hand back the finished application

# ===========================================================================
# RUNNING A NODE IN THE BACKGROUND (USED BY THE NOTEBOOK AND THE DEMONSTRATION SCRIPT)
# ===========================================================================

RUNNING = globals().get("RUNNING", {})   # background nodes by port; kept if this file is reloaded, so none are lost track of


def port_in_use(port: int) -> bool:
    """Return True if something on this computer is already listening on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:   # open a temporary network socket
        probe.settimeout(0.5)                                          # give up after half a second
        return probe.connect_ex((HOST, port)) == 0                     # 0 means the connection succeeded, so the port is taken


def first_free_port(start: int = DEFAULT_PORT, tries: int = 20) -> int:
    """Return the first port from start onwards that this program already runs a node on, or that nothing is using."""
    for port in range(start, start + tries):                           # try the preferred port first, then the ones after it
        if port in RUNNING or not port_in_use(port):                   # our own node can be replaced, and an unused port is free
            return port                                                # this port can be used
    raise RuntimeError(f"no free port between {start} and {start + tries - 1}")   # every port tried was taken by another program


class BackgroundNode:
    """One node running inside this program on a background thread."""

    def __init__(self, port: int, **options):
        """Create the platform and start serving it straight away."""
        self.port = port                                             # the port this node listens on
        self.url = f"http://{HOST}:{port}"                           # the base web address of the node
        self.platform = RemittancePlatform(**options)                # the platform behind the API
        self.app = create_app(self.platform, port)                   # the Flask application
        self.server = make_server(HOST, port, self.app, threaded=True)   # a web server that handles each request on its own thread
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)   # daemon=True lets the program exit without waiting
        self.thread.start()                                          # begin answering requests

    def stop(self):
        """Stop serving and release the port."""
        self.server.shutdown()             # ask the serving loop to finish
        self.server.server_close()         # close the listening socket so the port is free again
        self.thread.join()                 # wait for the background thread to end


def start_background_node(port: int = DEFAULT_PORT, **options) -> BackgroundNode:
    """Start a fresh node on the port, replacing any background node already running there."""
    if port in RUNNING:                                              # a node from an earlier run is still on this port
        RUNNING.pop(port).stop()                                     # stop it so the new node starts from a clean ledger
    if port_in_use(port):                                            # some other program holds the port
        raise RuntimeError(f"port {port} is already in use; close the program using it or choose another port")   # explain how to fix it
    logging.getLogger("werkzeug").setLevel(logging.ERROR)            # keep the web server's request log out of the output
    RUNNING[port] = BackgroundNode(port, **options)                  # start the node and remember it
    return RUNNING[port]                                             # hand it back to the caller


def stop_background_nodes():
    """Stop every background node this program started."""
    for port in list(RUNNING):             # list() copies the ports, because the dictionary shrinks as we go
        RUNNING.pop(port).stop()           # stop each node and forget it

# ===========================================================================
# RUNNING ONE NODE FROM THE COMMAND LINE
# ===========================================================================

def main():
    """Read the options from the command line and serve one node until Ctrl+C is pressed."""
    parser = argparse.ArgumentParser(description=f"Run one {ASSET} remittance node on this computer.")   # describes the program for --help
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on (default 5000)")   # where to listen
    parser.add_argument("--difficulty", type=int, default=DEMO_DIFFICULTY, help=f"leading zeros required of each block hash (default {DEMO_DIFFICULTY})")   # the mining rule
    parser.add_argument("--seed", help="derive repeatable keys from this text (demonstrations only); omit for random keys")   # repeatable keys
    parser.add_argument("--wall-clock", action="store_true", help="stamp records with the real time instead of the repeatable logical clock")   # which clock
    arguments = parser.parse_args()                                  # read what was typed
    if arguments.difficulty < MIN_DIFFICULTY:                        # the network's minimum from Assignment 7
        raise SystemExit(f"The difficulty must be at least {MIN_DIFFICULTY}.")   # stop with a clear message
    if port_in_use(arguments.port):                                  # refuse to start two nodes on one port
        raise SystemExit(f"Port {arguments.port} is already in use. Choose another with --port.")   # stop with a clear message
    clock = WallClock() if arguments.wall_clock else None            # None selects the repeatable logical clock
    platform = RemittancePlatform(difficulty=arguments.difficulty, seed=arguments.seed, clock=clock)   # build the platform
    app = create_app(platform, arguments.port)                       # wrap it in the API
    print(f"Node listening on http://{HOST}:{arguments.port}  (explorer at http://{HOST}:{arguments.port}/explorer)")   # where to find it
    print(f"Reserve agent {platform.reserve_agent.address}, genesis hash {platform.ledger.genesis_hash}")   # the two facts that identify the network
    app.run(host=HOST, port=arguments.port, threaded=True)           # serve on the loopback address only


if __name__ == "__main__":      # True only when this file is run directly, not when it is imported
    main()                      # start the node
