"""Literal two-kind policy; no caller supplied revision/path/schema choices."""
from types import MappingProxyType
SPECS=MappingProxyType({
 'repair98-retained/v1':MappingProxyType({'revision':'f70417d4bd70c3eb086ea63c90938dd2f43e0b99','build':'20261026.98','materialization':'retained-only','checks':frozenset({'p0','objects','ai','syntax','authority_native','systemd_native','runtime_isolation'}),'domain':'/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1'}),
 'corrected-fixture97-new/v1':MappingProxyType({'revision':'bc68ea0dc253425a44e268452130becdf35d764e','build':'20261026.97','materialization':'new-only','checks':frozenset({'p0','objects','ai','syntax','authority_native','systemd_native','runtime_isolation','cache','summaries','evaluation','serper_preparation'}),'domain':'/var/backups/careertrellis/preparation97-corrected-20261027/corrected97-fixture-g1','archive_sha256':'e91b4c04c4fc004ae7574ff091bd99c43e75ac6be15e84caa39d57d9dfa02e07','archive_bytes':9707520,'module_sha256':'db1604118386f23c9039fea1f3ccbec933f9a4d4e40ed7b9d9f37f7cde67fe13','work':'/opt/careertrellis/staging/preparation97-corrected-work-20261027/corrected97-fixture-g1','code':'/opt/careertrellis/staging/preparation97-corrected-code-20261027/corrected97-fixture-g1'})})
def exact_spec(kind):
 if type(kind) is not str or kind not in SPECS:raise RuntimeError('FIXED_SOURCE_KIND')
 return SPECS[kind]
