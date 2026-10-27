import datetime as dt
import unittest
from deploy.onedrive_retention import capacity_window as c, policy

B=85411840
START=dt.datetime(2030,1,1,tzinfo=dt.timezone.utc)


def arguments():
    return dict(observed_at=START.isoformat(),records=[],held_namespace_bytes=B,
        account_total_bytes=5*1024**3,account_free_bytes=4*1024**3,source_peak_bytes=B,
        uploads=[{'id':str(n),'at':(START+dt.timedelta(days=n)).isoformat(),'max_candidate_bytes':B} for n in range(12)])


class Tests(unittest.TestCase):
    def test_daily_full_grace_window_can_fit_but_sixth_overlap_cannot(self):
        result=c.forecast(**arguments())
        self.assertTrue(result['all_proposed_uploads_fit_optimistic_scenario']);self.assertTrue(result['simulation_only'])
        self.assertFalse(result['activation_viability_established'])
        self.assertEqual(result['scenario'],'optimistic_immediate_acceptance')
        self.assertIsNone(result['proven_acceptance_latency_bound_seconds'])
        self.assertNotIn('all_proposed_uploads_fit',result)
        self.assertFalse(result['native_actions_authorized'])
        self.assertEqual(max(event['capacity']['namespace_peak_bytes'] for event in result['events'] if event['kind']=='proposed_upload'),5*B)
        self.assertLess(5*B,policy.NAMESPACE_BUDGET)
        self.assertGreater(7*B,policy.NAMESPACE_BUDGET)
    def test_duplicate_weekly_growth_can_exhaust_namespace(self):
        args=arguments()
        # A small additional named hold fits daily cadence, but not overlap.
        args['held_namespace_bytes']+=30*1024**2
        self.assertTrue(c.forecast(**args)['all_proposed_uploads_fit_optimistic_scenario'])
        # Daily plus independently scheduled duplicate uploads every seven days.
        for day in (0,7):args['uploads'].append({'id':'weekly'+str(day),'at':(START+dt.timedelta(days=day,hours=1)).isoformat(),'max_candidate_bytes':B})
        args['uploads'].sort(key=lambda row:row['at'])
        result=c.forecast(**args)
        self.assertFalse(result['all_proposed_uploads_fit_optimistic_scenario'])
        self.assertIn('namespace_budget_exceeded',result['events'][-1]['capacity']['reasons'])
    def test_jitter_before_grace_is_not_credited(self):
        args=arguments();args['records']=[{'id':str(n),'accepted_at':(START-dt.timedelta(days=5-n)).isoformat(),'bytes':B,'held':False} for n in range(5)]
        args['uploads']=[{'id':'next','at':START.isoformat(),'max_candidate_bytes':B}]
        result=c.forecast(**args)
        self.assertTrue(result['all_proposed_uploads_fit_optimistic_scenario'])
        args['records'][-3]['accepted_at']=(START-dt.timedelta(days=1,seconds=1)).isoformat()
        args['records'][-2]['accepted_at']=(START-dt.timedelta(days=1)).isoformat()
        args['records'][-1]['accepted_at']=(START-dt.timedelta(seconds=1)).isoformat()
        # Hold oldest two too: no candidate eligible at this actual boundary.
        args['records'][0]['held']=args['records'][1]['held']=True
        result=c.forecast(**args)
        self.assertFalse(result['all_proposed_uploads_fit_optimistic_scenario'])
        self.assertEqual(result['events'][-1]['capacity']['namespace_peak_bytes'],7*B)
    def test_soft_deleted_bytes_keep_reducing_account_free(self):
        args=arguments();args['account_free_bytes']=2*1024**3
        args['uploads']=[{'id':str(n),'at':(START+dt.timedelta(days=n)).isoformat(),'max_candidate_bytes':B} for n in range(30)]
        soft=c.forecast(**args)
        self.assertFalse(soft['all_proposed_uploads_fit_optimistic_scenario'])
        self.assertIn('account_reserve_shortfall',soft['events'][-1]['capacity']['reasons'])
        self.assertTrue(all(event['simulated_account_credit_bytes']==0 for event in soft['events'] if event['kind']=='simulated_retirement'))
        hard=c.forecast(**args,assumed_permanent_reclamation=True)
        self.assertTrue(hard['all_proposed_uploads_fit_optimistic_scenario']);self.assertTrue(hard['assumed_permanent_reclamation'])
        self.assertFalse(hard['activation_viability_established'])
    def test_invalid_and_boolean_inputs_refuse(self):
        for value in (True,-1):
            args=arguments();args['held_namespace_bytes']=value
            with self.assertRaises(ValueError):c.forecast(**args)
        args=arguments();args['uploads'][1]['at']=args['uploads'][0]['at'];args['uploads'][1]['id']='0'
        with self.assertRaises(ValueError):c.forecast(**args)

    def test_six_hour_daily_acceptance_consumes_space_until_observed_horizon(self):
        result=c.forecast(**arguments(),acceptance_latency_seconds=6*3600)
        self.assertTrue(result['all_proposed_uploads_fit_supplied_scenario'])
        self.assertIsNone(result['all_proposed_uploads_fit_optimistic_scenario'])
        self.assertFalse(result['future_uploads_assumed_immediately_native_accepted'])
        self.assertEqual(result['scenario'],'supplied_acceptance_latency')
        self.assertEqual(result['pending_ids'],['11'])
        peak=max(event['capacity']['namespace_peak_bytes'] for event in result['events'] if event['kind']=='proposed_upload')
        self.assertEqual(peak,6*B)
        self.assertFalse(result['activation_viability_established'])
        self.assertIsNone(result['proven_acceptance_latency_bound_seconds'])

    def test_six_hour_latency_and_early_jitter_do_not_credit_grace_early(self):
        args=arguments();args['uploads'][5]['at']=(START+dt.timedelta(days=5,hours=-1)).isoformat()
        result=c.forecast(**args,acceptance_latency_seconds=6*3600)
        retired=[event for event in result['events'] if event['kind']=='simulated_retirement' and event['id']=='0']
        self.assertTrue(retired)
        self.assertEqual(retired[0]['at'],(START+dt.timedelta(days=4,hours=6)).isoformat())
        self.assertTrue(result['all_proposed_uploads_fit_supplied_scenario'])

    def test_six_hour_latency_with_weekly_duplicate_exceeds_budget(self):
        args=arguments()
        args['held_namespace_bytes']+=30*1024**2
        args['uploads'].append({'id':'weekly','at':(START+dt.timedelta(hours=1)).isoformat(),'max_candidate_bytes':B})
        args['uploads'].sort(key=lambda row:row['at'])
        result=c.forecast(**args,acceptance_latency_seconds=6*3600)
        self.assertFalse(result['all_proposed_uploads_fit_supplied_scenario'])
        self.assertIn('namespace_budget_exceeded',result['events'][-1]['capacity']['reasons'])
        self.assertEqual(result['events'][-1]['capacity']['namespace_peak_bytes'],6*B+30*1024**2)

    def test_never_accepted_archives_consume_budget_and_cannot_be_successors(self):
        result=c.forecast(**arguments(),acceptance_latency_seconds=None)
        self.assertFalse(result['all_proposed_uploads_fit_supplied_scenario'])
        self.assertEqual(result['pending_ids'],['0','1','2','3','4'])
        self.assertFalse(any(event['kind']=='simulated_retirement' for event in result['events']))
        self.assertFalse(any(event['kind']=='simulated_acceptance' for event in result['events']))

    def test_repeated_weekly_duplicate_with_latency_exceeds_without_extra_hold(self):
        args=arguments()
        for day in (0,7):
            args['uploads'].append({'id':'weekly'+str(day),
                'at':(START+dt.timedelta(days=day,hours=1)).isoformat(),'max_candidate_bytes':B})
        args['uploads'].sort(key=lambda row:row['at'])
        result=c.forecast(**args,acceptance_latency_seconds=6*3600)
        self.assertFalse(result['all_proposed_uploads_fit_supplied_scenario'])
        self.assertEqual(result['events'][-1]['id'],'weekly7')
        self.assertEqual(result['events'][-1]['capacity']['namespace_peak_bytes'],7*B)
        self.assertIn('namespace_budget_exceeded',result['events'][-1]['capacity']['reasons'])

    def test_existing_unaccepted_hold_and_per_upload_delays_remain_unverified(self):
        args=arguments();args['held_namespace_bytes']=0
        args['records']=[{'id':'unaccepted-hold','accepted_at':None,'bytes':B,'held':True}]
        args['uploads'][0]['acceptance_latency_seconds']=None
        args['uploads'][1]['acceptance_latency_seconds']=8*86400
        result=c.forecast(**args,acceptance_latency_seconds=6*3600)
        self.assertIn('unaccepted-hold',result['pending_ids'])
        self.assertIn('0',result['pending_ids']);self.assertIn('1',result['pending_ids'])
        self.assertFalse(result['all_proposed_uploads_fit_supplied_scenario'])
        self.assertFalse(any(event['kind']=='simulated_retirement' and event['id'] in ('0','1','unaccepted-hold')
                             for event in result['events']))

    def test_invalid_latency_assumptions_refuse(self):
        for value in (True,-1,0.5,'21600',366*86400):
            with self.subTest(value=value),self.assertRaises(ValueError):
                c.forecast(**arguments(),acceptance_latency_seconds=value)
            args=arguments();args['uploads'][0]['acceptance_latency_seconds']=value
            with self.subTest(per_upload=value),self.assertRaises(ValueError):c.forecast(**args)


if __name__=='__main__':unittest.main()
