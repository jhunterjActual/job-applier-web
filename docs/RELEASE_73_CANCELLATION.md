# Release 73: preserve Stop during search submission

Pressing Stop before the search POST finished could send cancellation before the
run existed, leaving the search running with Stop disabled. The UI now retains
that intent until submission completes and sends one cancellation for the exact
operation UUID. A delayed response cannot update a newer operation. An already
active different search requires a fresh Stop; intent is not transferred to it.

No database, worker, credentials, source catalog or discovery-mode changes.
Serper remains disabled. Build metadata and both launchers advance together to
`20260923.73`.

Offline browser regression cases exercise the real frontend functions with
delayed synthetic responses and blocked network. Required regression suite,
focused search tests, syntax checks, and loopback version/dashboard checks are
recorded in the root task acceptance receipt. Live cancellation remains a
separate gate until verified after exact deployment. Broad discovery coverage
and public beta activation remain open.
