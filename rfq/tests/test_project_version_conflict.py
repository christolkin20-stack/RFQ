import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rfq.models import Company, Project, UserCompanyProfile


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
class ProjectVersionConflictTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Version Co')
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='editor_version', password='pw12345')
        UserCompanyProfile.objects.create(user=self.user, company=self.company, role='editor', is_active=True)

        self.project = Project.objects.create(
            id='proj-version-1',
            company=self.company,
            name='Versioned Project',
            data={'id': 'proj-version-1', 'name': 'Versioned Project', 'items': [{'id': 'a'}]},
        )

        self.assertTrue(self.client.login(username='editor_version', password='pw12345'))

    def _origin(self):
        return {'HTTP_ORIGIN': 'http://testserver'}

    def _lock(self):
        res = self.client.post(
            '/api/locks/acquire',
            data=json.dumps({'resource_key': 'project:proj-version-1:edit', 'project_id': 'proj-version-1', 'context': 'project-data'}),
            content_type='application/json',
            **self._origin(),
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('acquired'))

    def test_stale_put_is_rejected_with_version_conflict_and_canonical_project(self):
        self._lock()
        current_version = self.project.updated_at.isoformat()

        first = {
            'id': 'proj-version-1',
            'name': 'First update',
            'items': [{'id': 'a'}, {'id': 'b'}],
            'base_version': current_version,
        }
        ok = self.client.put('/api/projects/proj-version-1', data=json.dumps(first), content_type='application/json', **self._origin())
        self.assertEqual(ok.status_code, 200)

        stale = {
            'id': 'proj-version-1',
            'name': 'Stale overwrite',
            'items': [{'id': 'a'}],
            'base_version': current_version,
        }
        blocked = self.client.put('/api/projects/proj-version-1', data=json.dumps(stale), content_type='application/json', **self._origin())
        self.assertEqual(blocked.status_code, 409)
        body = blocked.json()
        self.assertEqual(body.get('code'), 'version_conflict')
        self.assertEqual(body.get('project_id'), 'proj-version-1')
        self.assertIn('project', body)
        self.assertEqual(body.get('project', {}).get('name'), 'First update')
        self.assertTrue(body.get('server_version'))

    def test_stale_bulk_item_is_rejected(self):
        self._lock()
        base = self.project.updated_at.isoformat()

        first_bulk = {
            'projects': [{
                'id': 'proj-version-1',
                'name': 'Bulk First',
                'items': [{'id': 'a'}, {'id': 'b'}],
                'base_version': base,
            }]
        }
        ok = self.client.post('/api/projects/bulk', data=json.dumps(first_bulk), content_type='application/json', **self._origin())
        self.assertEqual(ok.status_code, 200)

        stale_bulk = {
            'projects': [{
                'id': 'proj-version-1',
                'name': 'Bulk Stale',
                'items': [{'id': 'a'}],
                'base_version': base,
            }]
        }
        blocked = self.client.post('/api/projects/bulk', data=json.dumps(stale_bulk), content_type='application/json', **self._origin())
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json().get('code'), 'version_conflict')

        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Bulk First')
