"""Provider-free installation policy and byte-inventory fences."""
import copy
from contextlib import contextmanager
import datetime as dt
import unittest
from unittest.mock import patch,Mock
from types import SimpleNamespace
from deploy import retention_install as i

NOW=dt.datetime(2026,10,27,14,tzinfo=dt.timezone.utc)


def plan():
    pair={'schema':'careertrellis.paired-maintenance-policy/v1','enabled':True,
          'manifest_revision':'a'*40,'application_release':'b'*40,'valid_until':'2027-01-23T00:00:00Z'}
    one={'schema':'careertrellis.onedrive-retention-policy/v1','enabled':True,
         'artifact_revision':'a'*40,'application_release':'b'*40,'valid_until':'2027-01-23T00:00:00Z',
         'paired_policy_sha256':i.sha(i.canonical(pair)),'reserve_percent':20,'peak_multiplier':4,
         'keep_verified':2,'grace_seconds':172800,'namespace_budget_bytes':512*1024**2}
    files={'/etc/systemd/system/'+name:{'source':'extras/'+name,'sha256':'4'*64,'mode':0o644}
           for name in ('careertrellis-onedrive-retention.service','careertrellis-onedrive-retention.timer')}
    return {'schema':'careertrellis.retention-install-plan/v1','transaction':'c'*32,'role':'web',
        'revision':'a'*40,'created_at':NOW.isoformat(),'expires_at':(NOW+dt.timedelta(minutes=10)).isoformat(),
        'packet_sha256':'d'*64,'installer_sha256':'8'*64,'baseline':{'application_current':'b'*40,'application_previous':'e'*40,
            'paired_revision':i.PAIR_REVISION,'paired_manifest_sha256':i.PAIR_MANIFEST,
            'paired_policy_sha256':i.PAIR_POLICY,'files':{name:None for name in files},'unit_dropins':{},
            'service_sources':{'careertrellis-paired-maintenance.service':'5'*64},
            'timer_sources':{name:'3'*64 for name in set(i.TIMERS['web'])|{'careertrellis-backup-health.timer'}}},
        'paired_policy':pair,'retention_policy':one,'files':files,
        'reviews':{'source_review':'f'*64,'native_controls':'1'*64,'readiness':'2'*64}}


class Tests(unittest.TestCase):
    def test_short_lived_exact_baseline_plan(self):
        value=plan();self.assertEqual(i.validate_plan(value,NOW),value)
        for mutate in (lambda p:p.update(expires_at=(NOW+dt.timedelta(minutes=16)).isoformat()),
            lambda p:p.update(created_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda p:p['baseline'].update(paired_revision='0'*40),
            lambda p:p['retention_policy'].update(reserve_percent=19),
            lambda p:p['retention_policy'].update(keep_verified=1),
            lambda p:p['retention_policy'].update(grace_seconds=60),
            lambda p:p['retention_policy'].update(paired_policy_sha256='0'*64),
            lambda p:p.update(reviews={'source_review':'f'*64})):
            value=plan();mutate(value)
            with self.assertRaises(i.Refused):i.validate_plan(value,NOW)

    def test_separately_staged_installer_hash_and_fixed_path_are_required(self):
        value=plan();raw=b'public synthetic installer bytes'
        value['installer_sha256']=i.sha(raw)
        packets=i.Path(__file__).resolve().parents[2]/'synthetic-packets'
        expected=packets/value['transaction']/'retention_install.py'
        with patch.object(i,'PACKETS',packets),patch.object(i,'__file__',str(expected)), \
             patch.object(i,'read',return_value=raw) as read:
            i.require_installer_source(value);read.assert_called_once_with(expected,0o644)
        with patch.object(i,'PACKETS',packets),patch.object(i,'__file__',str(expected)), \
             patch.object(i,'read',return_value=raw+b'drift'),self.assertRaises(i.Refused):
            i.require_installer_source(value)
        with patch.object(i,'PACKETS',packets),patch.object(i,'__file__',str(packets/'wrong.py')), \
             patch.object(i,'read') as read,self.assertRaises(i.Refused):
            i.require_installer_source(value)
        read.assert_not_called()

    def test_file_targets_and_complete_dropin_paths_are_bounded(self):
        value=plan();target='/usr/local/sbin/careertrellis-mirror-generated-objects'
        value['files'][target]={'source':'extras/careertrellis-mirror-generated-objects','sha256':'2'*64,'mode':0o755}
        value['baseline']['files'][target]='3'*64
        drop='/etc/systemd/system/careertrellis-backup-health.service.d/60-completed-alert-report.conf'
        value['baseline']['unit_dropins'][drop]='4'*64
        i.validate_plan(value,NOW)
        for target in ('/etc/passwd','/usr/local/sbin/careertrellis-deploy','/etc/systemd/system/caddy.service'):
            changed=plan();changed['files'][target]={'source':'extras/'+i.Path(target).name,'sha256':'2'*64,'mode':0o644}
            changed['baseline']['files'][target]='3'*64
            with self.assertRaises(i.Refused):i.validate_plan(changed,NOW)
        changed=copy.deepcopy(value);changed['files'][next(iter(changed['files']))]['source']='../private'
        with self.assertRaises(i.Refused):i.validate_plan(changed,NOW)
        changed=plan();changed['baseline']['unit_dropins']['/etc/systemd/system/caddy.service.d/override.conf']='4'*64
        with self.assertRaises(i.Refused):i.validate_plan(changed,NOW)

    def test_packet_validation_never_executes_incoming_source(self):
        source=b"FILES=frozenset(('entry.py',))\nraise RuntimeError('must not execute')\n"
        manifest=i.canonical({'schema':'test-schema','revision':'a'*40,'files':{'entry.py':i.sha(source)}})
        @contextmanager
        def folder(*args,**kwargs):yield 3
        def read(path,*args):return manifest if path.name=='manifest.json' else source
        with patch.object(i,'directory',folder),patch.object(i.os,'listdir',return_value=['entry.py','manifest.json']), \
             patch.object(i,'read',side_effect=read):
            files=i.package(i.Path('/synthetic'),'test-schema','a'*40)
            self.assertEqual(files['entry.py'],source)
        bad=i.canonical({'schema':'test-schema','revision':'a'*40,'files':{'entry.py':'0'*64}})
        with patch.object(i,'directory',folder),patch.object(i.os,'listdir',return_value=['entry.py','manifest.json']), \
             patch.object(i,'read',side_effect=lambda path,*_:bad if path.name=='manifest.json' else source):
            with self.assertRaises(i.Refused):i.package(i.Path('/synthetic'),'test-schema','a'*40)

    def test_duplicate_json_and_unknown_command_refuse_without_host_mutation(self):
        with self.assertRaises(i.Refused):i.decode(b'{"a":1,"a":2}\n')
        with patch.object(i,'install') as install,patch('builtins.print'):
            self.assertEqual(i.main(['activate']),1);install.assert_not_called()

    def test_first_install_initializes_complete_empty_census_and_never_repairs_unknown_state(self):
        @contextmanager
        def directory(*args,**kwargs):yield 7
        for role in ('web','db'):
            with patch.object(i,'directory',directory),patch.object(i.os,'listdir',return_value=[]), \
                 patch.object(i,'mkdir') as mkdir,patch.object(i,'write_once') as write:
                i.initialize_state(role)
                root=i.Path('/var/lib/careertrellis/onedrive-retention')/role
                self.assertIn(((root/'control.lock',b''),{}),write.call_args_list)
                if role=='db':
                    self.assertEqual({call.args[0] for call in mkdir.call_args_list},
                        {root/'catalog',root/'retirements',root/'schedule'}|{root/'catalog'/name for name in
                         ('accepted','intents','complete','holds','tombstones','tombstone-intents','tombstone-complete')})
                    self.assertIn(((root/'catalog'/'catalog.lock',b''),{}),write.call_args_list)
                else:self.assertEqual(mkdir.call_count,0)
        with patch.object(i,'directory',directory),patch.object(i.os,'listdir',return_value=['unknown']), \
             patch.object(i,'mkdir') as mkdir,patch.object(i,'write_once') as write:
            with self.assertRaises(i.Refused):i.initialize_state('db')
            mkdir.assert_not_called();write.assert_not_called()

    def test_first_phase_restores_old_mirror_and_preserves_disabled_full(self):
        previous={name:{'LoadState':'loaded','ActiveState':'active','UnitFileState':'enabled'}
                  for name in set(i.TIMERS['db'])|{'careertrellis-backup-health.timer'}}
        previous['careertrellis-pgbackrest-full.timer'].update(ActiveState='inactive',UnitFileState='disabled')
        actual={name:{'ActiveState':'inactive','UnitFileState':value['UnitFileState'],'Job':''}
                for name,value in previous.items()}
        started=[]
        def show(name,fields):return dict(actual[name])
        def command(args,timeout):
            self.assertEqual(args[:2],['/usr/bin/systemctl','start']);self.assertEqual(timeout,30)
            started.append(args[2]);actual[args[2]]['ActiveState']='active'
        with patch.object(i,'show',side_effect=show),patch.object(i,'command',side_effect=command), \
             patch.object(i,'write_once') as write:
            i.restore_prior_timers({'role':'db'},previous,i.Path('/synthetic'))
        self.assertIn('careertrellis-pgbackrest-onedrive.timer',started)
        self.assertNotIn('careertrellis-pgbackrest-full.timer',started)
        record=i.decode(write.call_args.args[1])
        self.assertFalse(record['old_mirror_timer_held']);self.assertFalse(record['legacy_timer_transition_performed'])
        self.assertFalse(record['retention_timer_activated'])

    def test_failed_old_attempt_blocks_new_transaction_before_timer_or_namespace_mutation(self):
        @contextmanager
        def directory(*args,**kwargs):yield 7
        @contextmanager
        def census(fd):yield iter([SimpleNamespace(name='d'*32),SimpleNamespace(name='reviews')])
        with patch.object(i,'directory',directory),patch.object(i.os,'scandir',side_effect=census), \
             patch.object(i,'idle') as idle,patch.object(i,'command') as command, \
             patch.object(i,'mkdir') as mkdir,patch.object(i,'write_once') as write:
            with self.assertRaisesRegex(i.Refused,'prior_attempt_requires_reconciliation'):
                i.preflight(plan())
            idle.assert_not_called();command.assert_not_called();mkdir.assert_not_called();write.assert_not_called()

    def test_baseline_publication_is_last_and_impossible_on_timer_restore_or_seal_failure(self):
        root=i.Path('/synthetic')
        complete={'standalone_manifest_sha256':'6'*64,'paired_manifest_sha256':'7'*64}
        restored=i.canonical({'schema':'careertrellis.retention-timer-restoration/v1',
            'old_mirror_timer_held':False,'legacy_timer_transition_performed':False,'retention_timer_activated':False})
        with patch.object(i,'restore_prior_timers',side_effect=i.Refused('restore_failed')), \
             patch.object(i,'read') as read,patch.object(i,'write_once') as write:
            with self.assertRaises(i.Refused):i.seal_after_restoration(plan(),{},root,complete)
            read.assert_not_called();write.assert_not_called()
        events=[];saved={}
        def write(path,raw):events.append(path);saved[path]=raw
        with patch.object(i,'restore_prior_timers',side_effect=lambda *args:events.append('restore')), \
             patch.object(i,'read',return_value=restored),patch.object(i,'write_once',side_effect=write):
            final=i.seal_after_restoration(plan(),{},root,complete)
        self.assertEqual(events,['restore',root/'complete.json',root/'sealed.json',i.ACCEPTED])
        self.assertTrue(final['prior_timer_states_restored'])
        self.assertEqual(final['timer_restoration_sha256'],i.sha(restored))
        self.assertEqual(i.decode(saved[i.ACCEPTED])['acceptance_receipts']['ops'],i.sha(saved[root/'complete.json']))
        events=[]
        def fail_seal(path,raw):
            events.append(path)
            if path==root/'sealed.json':raise OSError('synthetic seal failure')
        with patch.object(i,'restore_prior_timers'),patch.object(i,'read',return_value=restored), \
             patch.object(i,'write_once',side_effect=fail_seal):
            with self.assertRaises(OSError):i.seal_after_restoration(plan(),{},root,complete)
        self.assertNotIn(i.ACCEPTED,events)


if __name__=='__main__':unittest.main()
