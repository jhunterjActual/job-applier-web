# Campaign v1 compatibility fixture

`campaign_state_v1.py` preserves the exact controller from PR #101, commit
`4c7f2dc3cb43b5155b14de40a020bfa45e252545`, SHA-256
`cd339b8e9862dbf50e94ec82a9cc419467b5c3a769712ab79676f2066e57dab5`.

Only the synthetic v1 journal compatibility test loads this historical source.
It is not a live ledger, dispatch adapter, or provider integration.
