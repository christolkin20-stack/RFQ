from django.test import TestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=False)
class ApiSmokeTests(TestCase):
    def test_health_ok(self):
        r = self.client.get('/api/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get('ok'), True)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_projects_requires_auth_in_production_mode(self):
        r = self.client.get('/api/projects')
        self.assertEqual(r.status_code, 401)
        self.assertIn('error', r.json())

    @override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver'])
    def test_projects_requires_auth_in_debug_mode(self):
        r = self.client.get('/api/projects')
        self.assertEqual(r.status_code, 401)
        self.assertIn('error', r.json())

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_projects_post_requires_auth(self):
        r = self.client.post('/api/projects', data='{"name":"X"}', content_type='application/json')
        self.assertEqual(r.status_code, 401)
