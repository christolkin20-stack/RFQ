import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from rfq.models import Company, Project, SupplierAccess, SupplierInteractionFile, UserCompanyProfile


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
class SecurityHotfixTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name='Comp A')
        self.company_b = Company.objects.create(name='Comp B')

        User = get_user_model()
        self.user_a = User.objects.create_user(username='user_a', password='pw12345')
        self.user_b = User.objects.create_user(username='user_b', password='pw12345')

        UserCompanyProfile.objects.create(user=self.user_a, company=self.company_a, role='admin', is_active=True)
        UserCompanyProfile.objects.create(user=self.user_b, company=self.company_b, role='admin', is_active=True)

        self.project_a = Project.objects.create(id='proj-a', company=self.company_a, name='A', data={'id': 'proj-a', 'name': 'A', 'items': []})
        self.project_b = Project.objects.create(id='proj-b', company=self.company_b, name='B', data={'id': 'proj-b', 'name': 'B', 'items': []})

    def _origin(self):
        return {'HTTP_ORIGIN': 'http://testserver'}

    def test_export_denies_out_of_scope_project_ids(self):
        self.client.login(username='user_a', password='pw12345')
        payload = {'project_ids': ['proj-b'], 'format': 'csv'}
        r = self.client.post('/api/export', data=json.dumps(payload), content_type='application/json', **self._origin())
        self.assertEqual(r.status_code, 403)
        body = r.json()
        self.assertIn('denied_project_ids', body)
        self.assertEqual(body['denied_project_ids'], ['proj-b'])

    def test_supplier_interaction_file_download_is_tenant_scoped_for_authenticated_users(self):
        access_b = SupplierAccess.objects.create(
            id='tok-b',
            company=self.company_b,
            project=self.project_b,
            supplier_name='Supp B',
            requested_items=[],
            submission_data={},
            status='submitted',
            round=1,
        )
        uploaded = SimpleUploadedFile('secret.txt', b'secret-content', content_type='text/plain')
        f = SupplierInteractionFile.objects.create(
            company=self.company_b,
            supplier_access=access_b,
            round=1,
            file=uploaded,
            original_name='secret.txt',
            size=len(b'secret-content'),
            uploaded_by='supplier',
        )

        self.client.login(username='user_a', password='pw12345')
        r = self.client.get(f'/api/supplier_interaction/file/{f.id}', **self._origin())
        self.assertEqual(r.status_code, 403)

    def test_supplier_access_request_reopen_never_500(self):
        access_b = SupplierAccess.objects.create(
            id='tok-reopen',
            company=self.company_b,
            project=self.project_b,
            supplier_name='Supp B',
            requested_items=[{'id': 'x1'}],
            submission_data={},
            status='approved',
            round=1,
        )

        r = self.client.post(
            f'/api/supplier_access/{access_b.id}/request_reopen',
            data=json.dumps({'reason': 'Need correction'}),
            content_type='application/json',
            **self._origin(),
        )
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(r.status_code, 200)
        access_b.refresh_from_db()
        self.assertTrue((access_b.submission_data or {}).get('reopen_requested'))
