"""Public unit-template contracts; no unit is installed, enabled or started."""
import configparser
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/'systemd'


class Tests(unittest.TestCase):
    def document(self,name):
        parser=configparser.ConfigParser(interpolation=None,strict=True)
        parser.read_string((ROOT/name).read_text());return parser

    def test_finite_exact_root_entry_services(self):
        for suffix,operation,runtime,stop in (('','scheduled-run','36h','90s'),
                                             ('-health','health-web','5min','30s')):
            service=self.document('careertrellis-onedrive-retention'+suffix+'.service')['Service']
            self.assertEqual(service['ExecStart'],'/usr/bin/python3 -I -B /usr/local/libexec/careertrellis/onedrive_retention/entry.py '+operation)
            for key,value in {'Type':'exec','Restart':'no','RemainAfterExit':'no','User':'root','Group':'root',
                'UMask':'0077','RuntimeMaxSec':runtime,'TimeoutStartSec':'30s','TimeoutStopSec':stop,
                'KillMode':'control-group','SendSIGKILL':'yes','PrivateNetwork':'no'}.items():
                self.assertEqual(service[key],value)
            for key,value in {'ProtectSystem':'strict','ProtectHome':'yes','PrivateTmp':'yes',
                'NoNewPrivileges':'yes','DevicePolicy':'closed','DeviceAllow':'',
                'RestrictAddressFamilies':'AF_INET AF_UNIX',
                'CapabilityBoundingSet':'CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER CAP_SETGID CAP_SETUID',
                'AmbientCapabilities':'CAP_SETUID'}.items():self.assertEqual(service[key],value)
            self.assertEqual(service['MemoryMax'],'256M' if suffix else '1G')
            self.assertEqual(service['TasksMax'],'64' if suffix else '256')
            expected={'/run','/run/lock','/var/lib/careertrellis/paired-maintenance',
                      '/var/lib/careertrellis/onedrive-retention'}
            if not suffix:expected|={'/var/lib/careertrellis/generated-object-recovery',
                '/var/lib/careertrellis/generated-object-captures',
                '/var/lib/careertrellis/onedrive-retention-public','/var/backups/careertrellis'}
            self.assertEqual(set(service['ReadWritePaths'].split()),expected)

    def test_paired_dropin_only_adds_two_fixed_paths_preserving_guardian_lifetime(self):
        from deploy import retention_install as installer
        raw=(ROOT/'careertrellis-paired-maintenance.service.d/70-onedrive-retention-paths.conf').read_bytes()
        self.assertEqual(raw,installer.PAIRED_RW_BYTES)
        service=self.document('careertrellis-paired-maintenance.service.d/70-onedrive-retention-paths.conf')['Service']
        self.assertEqual(set(service),{'readwritepaths'})
        self.assertEqual(service['ReadWritePaths'].split(),[
            '/var/lib/careertrellis/onedrive-retention','/var/lib/careertrellis/onedrive-retention-public'])

    def test_calendar_and_relay_are_fixed_templates_not_activation(self):
        daily=self.document('careertrellis-onedrive-retention.timer')['Timer']
        relay=self.document('careertrellis-onedrive-retention-health.timer')['Timer']
        self.assertEqual(daily['OnCalendar'],'Mon..Sat *-*-* 06:00:00 UTC')
        self.assertEqual(relay['OnCalendar'],'*-*-* *:00/15:00 UTC')
        self.assertEqual(daily['Unit'],'careertrellis-onedrive-retention.service')
        self.assertEqual(relay['Unit'],'careertrellis-onedrive-retention-health.service')
        self.assertEqual(daily['Persistent'],'true');self.assertEqual(relay['Persistent'],'true')


if __name__=='__main__':unittest.main()
