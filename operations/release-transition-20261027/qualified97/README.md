# Qualified build97 preparation

This source prepares `56c8b137fd6c6e778a9f09e3b04b925edea6270e` (build `20261026.97`) as an inactive immutable release. Its fixed source kind is `qualified97-new/v1`. Application activation uses a separately reviewed transaction.

The publication branch is stacked on `ae4e5171b9f0c874721cce8db5170a5815d59e8e` (the reviewed inactive98 repair source). A comparison against hosted `main` therefore expects two commits. The same candidate commit must retain `operations/release-transition-20261027/repair98/repair_contract.py` with SHA256 `6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f`, as required by `source_boundary.py`.

The public Git prefix for these thirteen runtime files is `operations/release-transition-20261027/qualified97/`. `SOURCE_REGISTRY.json` records their exact bytes. The five entrypoint switches are enabled. Runtime behavior matches the independently reviewed V6 source after those five assignments; trailing whitespace and line endings were corrected without changing the Python AST. SOURCE_REGISTRY.json binds the final published bytes. The immutable public prepare_release291d.py fixture retains its exact Git CRLF bytes; its scoped whitespace rule keeps the default blank-line and trailing-space checks while recognizing CRLF. These switches do not authorize installation, host execution or plan allocation.

An operator must supply a reviewed protected source manifest, the actual accepted98 receipt and full protected chain, a fresh preflight, and a new unchanged plan/decision interval. The target97 release must be absent before exclusive materialization. Runtime hashes are derived after creation. Unknown or partial attempts are retained. The owner exits before a separate seal verifies original actor absence and unit/cgroup terminal state. Real receipt files, runtime inventories and operator configuration stay outside this source tree.

Run the portable checks from this directory:

```text
python -B -m unittest test_materialize97 test_source_boundary test_native97_unit test_runner97_catalog test_checks97 test_qualified_role_flows test_accepted98_dag test_registry_closure test_schema_occurrences test_materialization_chronology test_public_fixture_provenance
```

The baseline reviewed suite has36 tests; this projection has37, adding one public-fixture provenance check. Native boundaries are mocked in these portable checks. The synthetic receipt DAG uses2020 timestamps and synthetic actors; it contains no actual d91 records. `public-fixtures/PUBLIC_GIT_PROVENANCE.json` binds all eight code fixtures to exact public Git commits, paths and blob hashes. No baseline inventory is included.

The entrypoint roles are `preflight`, `decision-proposal`, `owner`, `coordinator` and `seal`. Decision proposal output is unallocated; protected input transport and exclusive allocation need separate review. No provider calls, system package installation, shared-browser mutation, application pointer changes or service activation are included in this preparation source.
