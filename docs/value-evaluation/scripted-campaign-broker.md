# Scripted Campaign broker and CT facade

This is local scripted preparation, not paid execution or native acceptance. There is no provider adapter, credential discovery, default counter/usage parser, process launcher or socket in these modules. Injected functions must be explicitly source-pinned ScriptedAdapter functions. No real response is authorized by a scripted fixture guard.

## Prerequisites and scope

This draft is based on PR114 head `2e10f7d620c4541b2da36f926360a949a3befe0d`. It includes the exact unchanged native cost-envelope source, tests and documentation blobs from PR104 `ae7f1c81c1982ecdd9295245c967afb603d73d6d`; that is content equivalence, not PR104 commit ancestry. Existing CI helper modules remain in both workflow lists; the cost and broker tests are appended. No application/database/UI behavior is changed.

Pinned dependencies are Campaign v2 source SHA-256 `b0394af9fa065f601840b53c03ccba39dc874e758c824503764917342fab16e6`, loaded budget arithmetic `a142625a224ff192c4c7093e144dc4993b459839e98777e700b85a1ae0b47a1c`, and native envelope `992f6a0b45200753f6071460d024e32a24dac14ed194f8a3b79f93f8b9f7d844`. Their raw working bytes were verified equal to Git blobs before adding exact-path `-text` attributes. Historical fixture byte policy remains intact. Hash expectations were not normalized or weakened.

## Explicit lifecycle

Create one Broker with a fresh inactive Campaign v2 handle before the owner begins the stage. Supply the five scripted functions and the explicitly pinned `reservation_calculator` keyword. Owner calls `begin_stage`; broker verifies the exact active product/case/stage/operation on every admission. The broker never begins or finishes a stage. Inspection reopening cannot dispatch; active, pending, halted and progressed controller histories refuse a new dispatcher. Same-process duplicate broker ownership also refuses. A future coordinator must establish exclusive ownership across processes and reconcile its exact private evidence root.

One shared live Broker supports the frozen Campaign capacity of one through four exchanges. It acquires slots before counting, refuses a fifth without counter/sender work, and holds no metadata lock across scripted external work. Count allowances are separately durable and do not reset after failure. Count completion after another call halts may be retained but cannot reserve or send a new generation. Full reservations and exact settled charges continue to use Campaign, with no legacy ledger adapter or replacement financial ledger.

Original request bytes and public headers are saved/fsynced/hash-read back before count/send. Authentication headers stay memory-only. A generation reservation and send intent precede one sender call. UNKNOWN retains exposure and unresolved slot ownership; no release/replay/reconciliation API is provided. Other already admitted known calls can settle after a global halt. Complete known charges settle before response guard/release; native refusal or later delivery failure cannot turn a bill into zero or financially unknown.

Public callback exceptions, including NativeRefused, follow the same count/reserved/settled state rules. Only a proven internally persisted terminal refusal may propagate as that classification. Evidence writes reserve their entire intended size before opening a file; partial writes and flush/fsync uncertainty retain the allowance. This conservative reservation counter is not measured physical storage or a provider-enforced money ceiling.

The one-use CT urllib facade preserves exact body/header snapshots and the caller's mandatory finite timeout from 1 through 90 seconds. Count/sender get only the remaining total deadline, never an increased 180-second default. Returning a buffered response, reading it or closing it does not trigger settlement. Returned bytes mean release to the facade, not native consumption, successful outputs, or completed stage.

## Durability and integration gates

Production directory fsync explicitly refuses Windows with `native_directory_fsync_unverified`; it never silently skips that operation. POSIX directory open/fsync failures propagate. Tests on Windows mock this boundary. Successful file fsync/readback tests do not prove durable directory-entry creation, crash recovery, actual Linux durability, or a usable Windows broker composition. No alternative durability backend is introduced here.

Missing acceptance includes native filesystem/ownership/durability, exact controller process ownership, actual count mapping and billing schemas, real JSON/SSE parser, supported pricing categories, actual response execution guard, authenticated hosted CT integration, native timeout/SSE concurrency behavior, durable host-mailbox delivery, complete outputs and owner funding approval. Injected scripts have cooperative deadline contracts; no hard cancellation or actual streaming network adapter is implemented. Logical dispatch admission is not proof of the first physical network byte or atomic cancellation. The proposed 1 GiB evidence limit is not a whole-batch storage proof or OS quota; actual resource planning remains separate.

## Synthetic checks

From `scripts/value_evaluation`, run:

    python -B -m unittest -v test_campaign_transport test_ct_campaign_opener

Both CI helper lists also retain the existing budget, CT wrapper and Campaign controls and include `test_native_cost_envelope`. Tests use private temporary synthetic ledgers and explicitly scripted bytes only. Controls cover four overlapping counts/fifth refusal, atomic count/slot ownership, known settlement after UNKNOWN, count-halt race, callback exceptions, reservation/settlement/evidence faults, conservative byte reservations, original-byte integrity and strict CT timeout/header/URL/no-replay behavior. They do not read credentials or call a provider.
