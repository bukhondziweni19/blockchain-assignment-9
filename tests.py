"""
tests.py
Assignment 9: Financial Blockchain Capstone (the rule tests)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS PROGRAM DOES
    Tests the rules directly, without the web server, including cases the demonstration
    does not reach: a forged block that has been mined again, a second spend waiting in
    the pool, a redemption by an unregistered address, and a competing chain.

        Group 1  the code carried forward from Assignments 1 to 7 still behaves as before
        Group 2  the rules for one stablecoin transaction
        Group 3  the rules for a whole chain, and the fork-choice rule
        Group 4  the platform: custody, the reserve and receipts

HOW TO RUN
    python tests.py          ends with a count such as "26 passed, 0 failed"

    The tests mine at difficulty 2, so the whole file runs in a second or two.
"""

import copy                 # copy.deepcopy makes an independent duplicate of a chain, so a test can damage it safely

from remittance import PlatformError, RemittancePlatform, receipt_signature_valid   # the platform, its error type and the receipt check
from simple_blockchain import (     # the chain core
    BLOCK_REWARD,                   # the miner's reward
    Transaction,                    # one transaction
    Wallet,                         # a key pair
    create_genesis_block,           # builds block 0
    make_coinbase,                  # builds a miner's reward
    mine_with_stats,                # the proof-of-work search
    tx_id,                          # the identifier of a transaction
)
from stablecoin import ASSET, Ledger, replay   # the asset name, the ledger and the full chain validation

TEST_DIFFICULTY = 2         # the lowest difficulty the network allows, so the tests run quickly
TESTS = []                  # every test function is added to this list by the decorator below

# ===========================================================================
# HELPERS
# ===========================================================================

def test(function):
    """Register a function as a test. Written above a function as @test."""
    TESTS.append(function)      # remember the function so the runner can call it
    return function             # hand the function back unchanged


def new_platform(**options) -> tuple:
    """Return a platform with repeatable keys and two hosted customers: (platform, sender address, recipient address)."""
    platform = RemittancePlatform(difficulty=TEST_DIFFICULTY, seed="tests", **options)   # repeatable keys, easy mining
    sender = platform.register_wallet("Sender")["address"]              # a hosted wallet for the paying customer
    recipient = platform.register_wallet("Recipient")["address"]        # a hosted wallet for the receiving customer
    return platform, sender, recipient                                  # everything a test needs to start


def signed(wallet: Wallet, ledger: Ledger, recipient: str, amount, kind: str = "transfer", sequence=None, timestamp: int = 1_790_009_999) -> dict:
    """Build and sign a stablecoin transaction outside the platform."""
    if sequence is None:                                                # normally the next sequence number is looked up
        sequence = ledger.account(wallet.address)["next_sequence"]      # the number the ledger expects
    tx = Transaction(sender=wallet.address, recipient=recipient, amount=amount, timestamp=timestamp,   # the four signed fields from Assignment 4
                     kind=kind, asset=ASSET, sequence=sequence, public_key=wallet.public_key_hex)     # and the capstone fields
    wallet.sign_transaction(tx)                                         # sign every field above
    return tx.to_dict()                                                 # the signed transaction as a dictionary


def funded_ledger() -> tuple:
    """Return (platform, ledger, Alice's wallet, Bob's address), where Alice keeps her own key and holds 100 mZAR."""
    platform, _, bob = new_platform()                                   # a platform with two hosted customers
    alice = Wallet.from_seed("tests/alice", "Alice")                    # a key the platform does not hold
    platform.register_wallet("Alice", alice.address)                    # registered by address only
    platform.on_ramp(alice.address, 100)                                # Alice deposits 100 rand
    platform.mine()                                                     # the mint is confirmed in block 1
    return platform, platform.ledger, alice, bob                        # everything a rule test needs


def mine_again(blocks: list, start: int):
    """Repair the links and proof of work of a chain from one block onwards, as a determined forger would."""
    for position in range(start, len(blocks)):                          # the edited block and every block after it
        blocks[position].previous_hash = blocks[position - 1].hash      # point at the (possibly new) hash of the parent
        mine_with_stats(blocks[position], blocks[position].difficulty)  # redo the proof of work


def refused(action, status: int) -> bool:
    """Return True if calling action() is refused by the platform with the expected status code."""
    try:                                    # the call is expected to raise a PlatformError
        action()                            # make the call
    except PlatformError as error:          # the platform refused it
        return error.status == status       # True only if it refused for the expected reason
    return False                            # the call went through, which the test did not expect

# ===========================================================================
# GROUP 1: THE CODE CARRIED FORWARD FROM ASSIGNMENTS 1 TO 7
# ===========================================================================

@test
def assignment_2_block_hash_is_unchanged():
    """The capstone extensions did not change how a block from Assignment 2 is hashed."""
    genesis = create_genesis_block(timestamp=1_700_000_000)     # the block printed in the Assignment 2 notebook
    assert genesis.hash.startswith("6b5ab8051e")                # the same hash as it had then


@test
def five_field_transaction_is_unchanged():
    """A transaction that uses none of the new fields looks and signs exactly as it did in Assignments 3 and 4."""
    tx = Transaction("alice", "bob", 10, timestamp=1)           # the five-field form
    assert list(tx.to_dict()) == ["sender", "recipient", "amount", "timestamp", "signature"]   # no new field appears
    assert tx.payload_bytes() == b'{"amount":10,"recipient":"bob","sender":"alice","timestamp":1}'   # the signed bytes are the same


@test
def seeded_keys_and_signatures_are_repeatable():
    """The same seed gives the same key and signature every time, and a random wallet differs."""
    first, second = Wallet.from_seed("tests/same"), Wallet.from_seed("tests/same")   # two wallets from one seed
    assert first.address == second.address                      # the same key, so the same address
    assert first.sign(b"message") == second.sign(b"message")    # deterministic signing gives the same signature
    assert Wallet.create().address != first.address             # a random key is different


@test
def a_signature_covers_every_field():
    """Changing any signed field, including a capstone field, breaks the signature."""
    wallet = Wallet.from_seed("tests/signer")                   # the signer
    tx = Transaction(wallet.address, "b" * 40, 5, timestamp=1, kind="transfer", asset=ASSET, sequence=0, public_key=wallet.public_key_hex)   # a full transaction
    wallet.sign_transaction(tx)                                 # sign it
    assert Wallet.verify(tx.payload_bytes(), tx.signature, wallet.public_key_hex)   # the untouched transaction verifies
    for field, value in (("amount", 6), ("recipient", "c" * 40), ("kind", "burn"), ("sequence", 1)):   # one change at a time
        altered = Transaction.from_dict({**tx.to_dict(), field: value})             # the same transaction with one field changed
        assert not Wallet.verify(altered.payload_bytes(), altered.signature, wallet.public_key_hex)   # the signature no longer holds

# ===========================================================================
# GROUP 2: THE RULES FOR ONE TRANSACTION
# ===========================================================================

@test
def a_valid_transfer_is_accepted_and_moves_value():
    """A correctly signed transfer is accepted, and after mining both balances change."""
    platform, ledger, alice, bob = funded_ledger()              # Alice holds 100 mZAR
    assert ledger.submit(signed(alice, ledger, bob, 40))[0] == "accepted"   # the transfer enters the pool
    platform.mine()                                             # it is mined into block 2
    assert ledger.account(alice.address)["confirmed"] == 60 and ledger.account(bob)["confirmed"] == 40   # 40 moved from Alice to Bob


@test
def only_the_reserve_agent_may_mint():
    """A mint signed by anyone other than the address in the genesis charter is refused."""
    _, ledger, alice, _ = funded_ledger()                       # Alice is an ordinary customer
    assert ledger.submit(signed(alice, ledger, alice.address, 1_000_000, kind="mint"))[0] == "unauthorised"   # she cannot create mZAR


@test
def nobody_can_spend_more_than_they_hold():
    """A transfer or burn larger than the balance is refused."""
    platform, ledger, alice, bob = funded_ledger()              # Alice holds 100 mZAR
    assert ledger.submit(signed(alice, ledger, bob, 101))[0] == "insufficient_funds"   # one unit too many
    assert ledger.submit(signed(alice, ledger, platform.reserve_agent.address, 101, kind="burn"))[0] == "insufficient_funds"   # the same for a burn


@test
def an_altered_transaction_is_refused():
    """A transaction changed after signing, or signed with somebody else's key, is refused."""
    _, ledger, alice, bob = funded_ledger()                     # Alice holds 100 mZAR
    mallory = Wallet.from_seed("tests/mallory")                 # an attacker with her own key
    genuine = signed(alice, ledger, bob, 10)                    # a transfer Alice really signed
    assert ledger.submit({**genuine, "amount": 100})[0] == "bad_signature"              # the amount was raised afterwards
    assert ledger.submit({**genuine, "recipient": mallory.address})[0] == "bad_signature"   # the recipient was swapped afterwards
    forged = {**signed(mallory, ledger, mallory.address, 10), "sender": alice.address}  # signed by Mallory, relabelled as Alice
    assert ledger.submit(forged)[0] == "bad_signature"          # Mallory's key does not belong to Alice's address


@test
def a_transaction_cannot_be_replayed():
    """The same signed transaction is refused a second time, both while pending and after it is mined."""
    platform, ledger, alice, bob = funded_ledger()              # Alice holds 100 mZAR
    transfer = signed(alice, ledger, bob, 10)                   # a genuine transfer
    assert ledger.submit(transfer)[0] == "accepted"             # accepted the first time
    assert ledger.submit(transfer)[0] == "replay"               # refused while it is still pending
    platform.mine()                                             # mine it
    assert ledger.submit(transfer)[0] == "replay"               # refused again once it is confirmed


@test
def sequence_numbers_must_be_used_in_order():
    """A transaction carrying a sequence number that is too low or too high is refused."""
    platform, ledger, alice, bob = funded_ledger()              # Alice has sent nothing, so her next number is 0
    assert ledger.submit(signed(alice, ledger, bob, 10, sequence=1))[0] == "replay"   # a gap: number 0 was skipped
    assert ledger.submit(signed(alice, ledger, bob, 10, sequence=0))[0] == "accepted" # the right number
    assert ledger.submit(signed(alice, ledger, bob, 20, sequence=0))[0] == "replay"   # number 0 used again for a different payment
    assert ledger.account(alice.address)["next_sequence"] == 1  # the pending transfer already counts


@test
def two_pending_spends_cannot_overdraw():
    """A second transfer is checked against what the first one, still pending, will leave behind."""
    _, ledger, alice, bob = funded_ledger()                     # Alice holds 100 mZAR
    assert ledger.submit(signed(alice, ledger, bob, 60))[0] == "accepted"             # 40 will remain
    assert ledger.submit(signed(alice, ledger, bob, 60))[0] == "insufficient_funds"   # so a second 60 is refused before any block is mined


@test
def malformed_transactions_are_refused():
    """Decimals, booleans, unknown fields, null fields and a transfer to oneself are all refused as malformed."""
    platform, ledger, alice, bob = funded_ledger()              # Alice holds 100 mZAR
    assert ledger.submit(signed(alice, ledger, bob, 2.5))[0] == "malformed"             # a decimal amount
    assert ledger.submit(signed(alice, ledger, bob, True))[0] == "malformed"            # a boolean, which Python would count as 1
    assert ledger.submit({**signed(alice, ledger, bob, 10), "note": "hello"})[0] == "malformed"   # a field outside the schema
    assert ledger.submit({**signed(alice, ledger, bob, 10), "reference": None})[0] == "malformed" # a field set to null
    assert ledger.submit(signed(alice, ledger, alice.address, 10))[0] == "malformed"    # paying yourself
    assert ledger.submit(signed(alice, ledger, bob, 10, kind="burn"))[0] == "unauthorised"   # a burn not addressed to the reserve agent
    assert ledger.submit("not a transaction")[0] == "malformed" # not even a JSON object
    assert ledger.pending == []                                 # nothing refused ever entered the pool

# ===========================================================================
# GROUP 3: THE RULES FOR A WHOLE CHAIN
# ===========================================================================

def payment_chain() -> tuple:
    """Return (platform, a copy of its chain) after one full payment: mint in block 1, transfer in block 2, burn in block 3."""
    platform, sender, recipient = new_platform()                # two hosted customers
    platform.pay(sender, recipient, 750)                        # on-ramp, transfer and off-ramp, one block each
    return platform, copy.deepcopy(platform.ledger.chain)       # the platform and an independent copy of its blocks


@test
def a_valid_chain_replays_to_the_right_state():
    """Replaying the chain rebuilds the balances, and the mZAR supply equals the balances added together."""
    platform, blocks = payment_chain()                          # a chain holding one full payment
    state, problem = replay(blocks, platform.ledger.genesis_hash)   # validate and rebuild the account table
    assert problem is None                                      # the chain is valid
    assert (state.minted, state.burned, state.supply) == (750, 750, 0)   # everything minted was later burned
    assert sum(state.balances.values()) == state.supply         # no mZAR exists outside the balances
    assert state.rewards[platform.validator.address] == 3 * BLOCK_REWARD   # three blocks, three rewards, paid in the native coin


@test
def an_edited_block_is_detected():
    """Changing a confirmed amount breaks the block's stored hash."""
    _, blocks = payment_chain()                                 # a valid chain
    blocks[2].transactions[1]["amount"] = 7500                  # raise the confirmed transfer from 750 to 7,500
    assert "self-hash pillar failed" in replay(blocks)[1]       # the stored hash no longer matches the contents


@test
def an_edited_block_that_is_mined_again_is_still_detected():
    """Redoing the proof of work does not help a forger, because the signature no longer matches."""
    _, blocks = payment_chain()                                 # a valid chain
    blocks[2].transactions[1]["amount"] = 7500                  # the same edit as before
    mine_again(blocks, 2)                                       # repair every hash and link from block 2 onwards
    assert "signature does not match" in replay(blocks)[1]      # the hashes are consistent, but the Sender never signed 7,500


@test
def a_miner_cannot_pay_itself_more_or_twice():
    """A larger reward, or a second reward in one block, invalidates the block even after it is mined again."""
    _, blocks = payment_chain()                                 # a valid chain
    inflated = copy.deepcopy(blocks)                            # a copy to damage
    inflated[1].transactions[0]["amount"] = 1_000_000           # the miner awards itself a million coins
    mine_again(inflated, 1)                                     # and redoes the proof of work
    assert "coinbase is not the reward the rules allow" in replay(inflated)[1]   # the reward is rebuilt and compared
    doubled = copy.deepcopy(blocks)                             # another copy to damage
    doubled[1].transactions.append(make_coinbase("a" * 40, BLOCK_REWARD, doubled[1].timestamp, 1))   # a second reward in block 1
    mine_again(doubled, 1)                                      # and redoes the proof of work
    assert "extra coinbase" in replay(doubled)[1]               # only one reward per block


@test
def an_under_mined_block_is_refused():
    """A block mined to a lower difficulty than the genesis block declares is refused."""
    platform = RemittancePlatform(difficulty=3, seed="tests")   # a network that demands three leading zeros
    platform.mine()                                             # one honest block
    blocks = copy.deepcopy(platform.ledger.chain)               # a copy to damage
    blocks[1].difficulty = 2                                    # the block now declares only two zeros
    mine_with_stats(blocks[1], 2)                               # and is mined to that easier target
    assert "below the network's 3" in replay(blocks)[1]         # the genesis block's difficulty still applies


@test
def the_charter_fixes_who_may_mint():
    """Swapping the reserve agent in the genesis block gives a different network, on which the old mints are invalid."""
    platform, blocks = payment_chain()                          # a valid chain
    mallory = Wallet.from_seed("tests/mallory")                 # an attacker who wants to become the reserve agent
    forged = Ledger(mallory.address, TEST_DIFFICULTY).chain[0]  # a genesis block naming the attacker instead
    assert forged.hash != platform.ledger.genesis_hash          # the genesis hash commits to the reserve agent
    swapped = [forged] + blocks[1:]                             # the attacker's genesis under the real blocks
    mine_again(swapped, 1)                                      # with every link and proof of work repaired
    assert "Genesis: does not match" in replay(swapped, platform.ledger.genesis_hash)[1]   # a verifier who knows the real genesis hash stops here
    assert "only the reserve agent" in replay(swapped)[1]       # and without it, the original mint is no longer authorised


@test
def the_fork_choice_rule_validates_before_comparing():
    """A longer valid chain is adopted, a longer invalid chain never is, and a chain of equal length changes nothing."""
    first = RemittancePlatform(difficulty=TEST_DIFFICULTY, seed="tests")    # one node
    second = RemittancePlatform(difficulty=TEST_DIFFICULTY, seed="tests")   # a second node with the same genesis block
    assert first.ledger.genesis_hash == second.ledger.genesis_hash         # the same seed gives the same reserve agent
    customer = first.register_wallet("Customer")["address"]                # a customer known to the first node
    first.on_ramp(customer, 500)                                           # a deposit and mint on the first node
    first.mine()                                                           # block 1 on the first node
    first.mine()                                                           # block 2 on the first node
    longer = copy.deepcopy(first.ledger.chain)                             # the first node's chain: three blocks
    assert second.ledger.consider_chain(longer) == (True, "adopted the longer valid chain")   # longer and valid, so adopted
    assert second.ledger.account(customer)["confirmed"] == 500             # the second node now sees the customer's balance
    assert second.ledger.consider_chain(longer)[0] is False                # the same chain again is not longer
    invalid = copy.deepcopy(longer)                                        # a copy to damage
    invalid[1].transactions[1]["amount"] = 5000                            # inflate the mint
    mine_again(invalid, 1)                                                 # repair the hashes
    invalid.append(copy.deepcopy(invalid[-1]))                             # make it longer than the honest chain
    invalid[-1].index += 1                                                 # the extra block takes the next position
    invalid[-1].transactions = [make_coinbase(first.validator.address, BLOCK_REWARD, invalid[-1].timestamp, invalid[-1].index)]   # with its own reward
    mine_again(invalid, len(invalid) - 1)                                  # link and mine the extra block
    adopted, message = second.ledger.consider_chain(invalid)               # offer the longer, invalid chain
    assert adopted is False and "invalid" in message                       # length cannot make up for a forged mint

# ===========================================================================
# GROUP 4: THE PLATFORM
# ===========================================================================

@test
def the_reserve_never_falls_below_the_supply():
    """At every step of a payment, including the moments between a request and its block, the reserve covers the supply."""
    platform, sender, recipient = new_platform()                # two hosted customers
    steps = [lambda: platform.on_ramp(sender, 1000), platform.mine,                 # deposit, then confirm the mint
             lambda: platform.transfer(sender, recipient, 750), platform.mine,      # transfer, then confirm it
             lambda: platform.off_ramp(recipient, 750), platform.mine]              # burn, then confirm it and pay out
    for action in steps:                                        # run the payment one step at a time
        action()                                                # carry out the step
        report = platform.reserve_report()                      # compare the cash book with the chain
        assert report["fully_backed"] and report["surplus"] >= 0    # the reserve rule holds after every single step
    assert (report["reserve_balance"], report["supply"]) == (250, 250)   # and ends exactly matched


@test
def a_receipt_is_signed_and_cannot_be_altered():
    """The reserve agent's signature covers the whole receipt."""
    platform, sender, recipient = new_platform()                # two hosted customers
    platform.pay(sender, recipient, 750)                        # a full payment, which issues one receipt
    receipt = platform.find_record("RCPT-0001")                 # the receipt
    agent = platform.reserve_agent.address                      # the address named in the genesis charter
    assert receipt_signature_valid(receipt, agent)              # the genuine receipt verifies
    assert not receipt_signature_valid({**receipt, "paid_out": 7500}, agent)        # a changed amount does not
    assert not receipt_signature_valid(receipt, recipient)      # and it only verifies against the reserve agent's address


@test
def the_platform_signs_only_for_keys_it_holds():
    """Hosted actions are refused for a self-custody wallet, and the on-ramp is refused for an unregistered address."""
    platform, sender, recipient = new_platform()                # two hosted customers
    own_key = Wallet.from_seed("tests/own-key")                 # a customer who keeps their own key
    platform.register_wallet("Own key", own_key.address)        # registered by address only
    assert refused(lambda: platform.transfer(own_key.address, recipient, 1), 403)   # the platform cannot sign a transfer for it
    assert refused(lambda: platform.off_ramp(own_key.address, 1), 403)              # nor a burn
    assert refused(lambda: platform.on_ramp("f" * 40, 100), 403)                    # an unregistered address cannot deposit
    assert refused(lambda: platform.register_wallet("Sender"), 409)                 # a name can be registered only once
    assert refused(lambda: platform.on_ramp(sender, 2.5), 400)                      # a decimal amount is refused before any money moves
    assert platform.reserve.balance == 0 and platform.ledger.pending == []          # none of the refusals changed anything


@test
def a_self_custody_customer_can_redeem():
    """A registered customer who holds their own key signs the burn themselves and is still paid out."""
    platform, ledger, alice, _ = funded_ledger()                # Alice keeps her own key and holds 100 mZAR
    platform.submit_signed(signed(alice, ledger, platform.reserve_agent.address, 30, kind="burn"))   # Alice signs her own burn
    receipt = platform.mine()["receipts_issued"][0]             # mining confirms it and triggers the payout
    assert (receipt["status"], receipt["paid_out"], platform.reserve.balance) == ("paid", 30, 70)   # paid, and the reserve fell by 30


@test
def an_unregistered_redemption_is_held():
    """mZAR burned by an address that was never registered is not paid out, and the reserve stays over-backed."""
    platform, ledger, alice, _ = funded_ledger()                # Alice holds 100 mZAR
    stranger = Wallet.from_seed("tests/stranger")               # an address the platform has never registered
    platform.submit_signed(signed(alice, ledger, stranger.address, 30))             # Alice pays the stranger 30 mZAR
    platform.mine()                                             # confirm the transfer
    platform.submit_signed(signed(stranger, ledger, platform.reserve_agent.address, 30, kind="burn"))   # the stranger burns it
    receipt = platform.mine()["receipts_issued"][0]             # mining confirms the burn
    assert receipt["status"].startswith("held") and receipt["paid_out"] == 0        # no payout to an unknown party
    assert platform.reserve_report()["surplus"] == 30           # the 30 rand stays in reserve, above the remaining supply


@test
def a_payout_waits_for_the_required_confirmations():
    """With two confirmations required, the block holding the burn pays nothing; the next block does."""
    platform, sender, recipient = new_platform(confirmations_required=2)   # a stricter finality rule
    platform.on_ramp(sender, 100)                               # a deposit
    platform.mine()                                             # block 1: the mint, with one confirmation
    platform.transfer(sender, recipient, 100)                   # accepted, because the pool applies the same rules as a block
    platform.off_ramp(recipient, 100)                           # the burn follows in the same pool
    assert platform.mine()["receipts_issued"] == []             # block 2 holds the burn but pays nothing yet
    assert platform.reserve.balance == 100                      # the money is still in reserve
    assert len(platform.mine()["receipts_issued"]) == 1         # block 3 gives the burn its second confirmation
    assert platform.reserve.balance == 0                        # and only now is the payout made


@test
def two_runs_with_the_same_seed_are_identical():
    """The whole chain, every identifier included, is the same on every run."""
    hashes = []                                                 # the block hashes of each run
    for _ in range(2):                                          # run the same payment twice
        platform, sender, recipient = new_platform()            # a fresh platform each time
        platform.pay(sender, recipient, 750)                    # the same payment
        hashes.append([block.hash for block in platform.ledger.chain])   # record every block hash
    assert hashes[0] == hashes[1]                               # identical, block for block

# ===========================================================================
# RUNNING THE TESTS
# ===========================================================================

def run_tests() -> int:
    """Run every registered test, print one line for each, and return how many failed."""
    failures = 0                                                # nothing has failed yet
    for function in TESTS:                                      # run the tests in the order they are written
        name = function.__name__.replace("_", " ")              # turn the function name into a readable sentence
        try:                                                    # an assert that fails raises an error
            function()                                          # run the test
            print(f"  [PASS] {name}")                           # every assert held
        except Exception as error:                              # an assert failed, or the test crashed
            failures += 1                                       # count the failure
            print(f"  [FAIL] {name}: {type(error).__name__} {error}")   # say which test and why
    print(f"\n{len(TESTS) - failures} passed, {failures} failed")   # the final count
    return failures                                             # the caller turns this into an exit status


if __name__ == "__main__":      # True only when this file is run directly, not when it is imported
    raise SystemExit(1 if run_tests() else 0)   # exit with an error status if any test failed
