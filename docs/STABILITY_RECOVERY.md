# Co-op+ stability candidate

This candidate is for a **new paired run**, with Shared income enabled and the
proxy's pooled shared gold disabled. Both peers need the same candidate. Existing
v1 income runs retain their accounting and strict matching-save boundary; an
update cannot recreate acknowledged payments that those runs already discarded.

## Income recovery

The new protocol saves a run, recipient and applied-payment receipt on the same
native body as the wallet. On rejoin it reads that body, rather than assuming a
newer world-global counter proves the older body contains the money. A fresh
session handshake rejects stale payments and acknowledgements. The host retains
up to 65,536 ordered payments per account, including acknowledged payments, and
replays missing income into the saved personal balance. Personal spending and
separate wallets remain independent. Dead players continue earning.

The intended recovery case is a client game terminating abruptly while the host
game and proxies remain running, then rejoining with the original identity and
an older coherent body checkpoint inside that payment window. Clean paired
Continue and both clean game restarts with proxies retained use the same body
validation. A client proxy restart also requires the original network identity.
Steam identities persist; the local LAN transport assigns a new peer ID on a
proxy reconnect, so that LAN case is refused. It needs a new paired run rather
than an automatic accounting reset. Native test scope is recorded in the review
handoff; same-identity Steam proxy restart is not inferred from a LAN test.

Host rollback, different runs/recipients, missing body receipts, corrupt or mixed
journal pages, and checkpoints older than the retained window fail closed with
an actionable message. No receipt clearing, wallet balancing adjustment or
automatic run reset is performed. Pending money is never evicted to make room;
collection waits with an explanation when retention is exhausted.

Gold waiting for acceptance remains an identified world entity. Its pickup is
gated and native attachment after the callback is undone before rendering/saving.
Only exact shared gold is handled; unpaid gold is not deleted. Authoritative
retirement determines when a collected nugget can be removed. Chest gold uses
stable event/source identities so native replay cannot create a second payment
for the same output. Non-money chest loot retains its existing per-peer behavior.

## Durability limits

This is income recovery, not a transaction log for every game action. Restoring
an older character can also roll back purchases, inventory, health and world
changes. The saved body's personal balance is the starting point; later spending
that Noita never checkpointed may roll back with the rest of that character.
Lost blood-money healing or cosmetic effects cannot always be reconstructed when
the old checkpoint has no matching pickup/life receipt. No arbitrary saved-run
repair, host-crash atomicity or sudden-power-loss guarantee is made.

The native checkpoint must contain its body and all income Globals/pages. Page
revisions detect mixed/missing data; unreadable state is not silently replaced.
The payment window is measured in payments, not minutes. Pickup identities are
retained independently for replay protection.

## Tinker and pickup cost

A positive legitimate Tinker count now authorizes repairing a missing native
effect or icon. The repair does not increment the count or repeat perk pickup.
Native no-edit effects, spectator state and polymorph restrictions still apply.
Physical spell movement remains a human acceptance check when automated dragging
does not actually move a spell.

Incremental persistence encodes changed history pages instead of all retained
pickup IDs on every phase. The review includes comparable 0/1,000/3,200/10,000
history, wallet and burst measurements, including deferred work. These measure
serialization CPU, not a guarantee about native frame rate or unrelated EW errors.

Do not publish this candidate as gameplay Latest or deploy it automatically.
Use the complete updater-compatible candidate for independent review and a fresh
two-player playtest after reviewing its specific native results and limits.
