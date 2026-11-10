"""Explicit synthetic unit-array fixtures; never an actual-property default."""
import copy
def typed_arrays(name,next_realtime,monotonic=None):
 if name=='careertrellis-paired-maintenance.timer':return {'calendar_timers':[{'base':'OnCalendar','specification':'Sun *-*-* 03:00:00 America/New_York','next_elapse_usec':next_realtime}],'monotonic_timers':[]}
 assert name=='careertrellis-paired-recovery.timer'
 rows=[{'base':'OnBootUSec','offset_usec':30000000,'next_elapse_usec':30000000},{'base':'OnUnitActiveUSec','offset_usec':60000000,'next_elapse_usec':2000000}] if monotonic is None else monotonic
 return {'calendar_timers':[],'monotonic_timers':copy.deepcopy(rows)}
