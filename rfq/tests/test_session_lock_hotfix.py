import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rfq.models import AuditLog, Company, Project, UserCompanyProfile


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
class SessionAndLockHotfixTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name='Comp A')
        self.company_b = Company.objects.create(name='Comp B')

        User = get_user_model()
        self.admin_a = User.objects.create_user(username='admin_a_hotfix', password='pw12345')
        self.editor_a = User.objects.create_user(username='editor_a_hotfix', password='pw12345')
        self.admin_b = User.objects.create_user(username='admin_b_hotfix', password='pw12345')

        UserCompanyProfile.objects.create(user=self.admin_a, company=self.company_a, role='admin', is_active=True)
        UserCompanyProfile.objects.create(user=self.editor_a, company=self.company_a, role='editor', is_active=True)
        UserCompanyProfile.objects.create(user=self.admin_b, company=self.company_b, role='admin', is_active=True)

        self.project_a = Project.objects.create(
            id='proj-hotfix-a',
            company=self.company_a,
            name='Proj A',
            data={'id': 'proj-hotfix-a', 'name': 'Proj A', 'items': []},
        )

    def _origin(self):
        return {'HTTP_ORIGIN': 'http://testserver'}

    def test_multitab_logout_immediately_blocks_write_calls(self):
        tab_a = self.client
        tab_b = self.client_class()

        self.assertTrue(tab_a.login(username='admin_a_hotfix', password='pw12345'))
        session_cookie_name = settings.SESSION_COOKIE_NAME
        tab_b.cookies[session_cookie_name] = tab_a.cookies[session_cookie_name].value

        # Logout in tab A invalidates session for all tabs sharing same cookie.
        r_logout = tab_a.get('/logout/?next=/login/')
        self.assertEqual(r_logout.status_code, 302)

        payload = {'projects': [{'id': 'proj-hotfix-a', 'name': 'Mutation after logout', 'items': []}]}
        r_write = tab_b.post('/api/projects/bulk', data=json.dumps(payload), content_type='application/json', **self._origin())
        self.assertEqual(r_write.status_code, 401)

    def test_lock_conflict_rejects_parallel_project_write(self):
        c1 = self.client
        c2 = self.client_class()
        self.assertTrue(c1.login(username='admin_a_hotfix', password='pw12345'))
        self.assertTrue(c2.login(username='editor_a_hotfix', password='pw12345'))

        acquire = c1.post(
            '/api/locks/acquire',
            data=json.dumps({'resource_key': 'project:proj-hotfix-a:view:item-detail', 'project_id': 'proj-hotfix-a', 'context': 'item-detail'}),
            content_type='application/json',
            **self._origin(),
        )
        self.assertEqual(acquire.status_code, 200)
        self.assertTrue(acquire.json().get('acquired'))

        payload = {'projects': [{'id': 'proj-hotfix-a', 'name': 'Concurrent overwrite', 'items': []}]}
        blocked = c2.post('/api/projects/bulk', data=json.dumps(payload), content_type='application/json', **self._origin())
        self.assertEqual(blocked.status_code, 409)
        body = blocked.json()
        self.assertEqual(body.get('code'), 'lock_conflict')
        self.assertEqual(body.get('project_id'), 'proj-hotfix-a')
        self.assertIn('project', body)

        self.project_a.refresh_from_db()
        self.assertEqual(self.project_a.name, 'Proj A')

    def test_admin_force_unlock_own_company_emits_audit_log(self):
        self.assertTrue(self.client.login(username='admin_a_hotfix', password='pw12345'))

        acquire = self.client.post(
            '/api/locks/acquire',
            data=json.dumps({'resource_key': 'rk-audit-1', 'project_id': 'proj-hotfix-a'}),
            content_type='application/json',
            **self._origin(),
        )
        self.assertEqual(acquire.status_code, 200)

        force = self.client.post(
            '/api/locks/force_unlock',
            data=json.dumps({'resource_key': 'rk-audit-1'}),
            content_type='application/json',
            **self._origin(),
        )
        self.assertEqual(force.status_code, 200)
        self.assertTrue(force.json().get('forced'))

        self.assertTrue(
            AuditLog.objects.filter(action='lock.force_unlock', entity_type='lock', entity_id='rk-audit-1', actor=self.admin_a).exists()
        )
