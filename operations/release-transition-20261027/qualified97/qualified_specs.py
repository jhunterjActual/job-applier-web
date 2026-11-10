"""Literal two-kind policy; no caller supplied revision/path/schema choices."""
from types import MappingProxyType
SPECS=MappingProxyType({
 'repair98-retained/v1':MappingProxyType({'revision':'f70417d4bd70c3eb086ea63c90938dd2f43e0b99','build':'20261026.98','materialization':'retained-only','checks':frozenset({'p0','objects','ai','syntax','authority_native','systemd_native','runtime_isolation'}),'domain':'/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1'}),
 'qualified97-new/v1':MappingProxyType({'revision':'56c8b137fd6c6e778a9f09e3b04b925edea6270e','build':'20261026.97','materialization':'new-only','checks':frozenset({'p0','objects','ai','syntax','authority_native','systemd_native','runtime_isolation','cache','summaries','evaluation','serper_preparation'}),'domain':'/var/backups/careertrellis/preparation97-qualified-20261027/qualified97-fixture-g1','archive_sha256':'767c0bbcff86c410d4fecfcb3275b09c486d0a0d20c20047a3dd6909cac3306a','archive_bytes':9707520,'module_sha256':'86e52d2e9fbc15acc28e94af5beb607cdf278bb4a091b50899e77ccd91dcafdc','work':'/opt/careertrellis/staging/preparation97-qualified-work-20261027/qualified97-fixture-g1','code':'/opt/careertrellis/staging/preparation97-qualified-code-20261027/qualified97-fixture-g1'})})
def exact_spec(kind):
 if type(kind) is not str or kind not in SPECS:raise RuntimeError('FIXED_SOURCE_KIND')
 return SPECS[kind]
