from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import path, reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APITestCase
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

USERNAME = "merchant"
EMAIL = "merchant@example.com"
PASSWORD = "Str0ng-Passphrase!"


class RegisterTests(APITestCase):
    url = reverse("register")

    def payload(self, **overrides):
        return {"username": USERNAME, "email": EMAIL, "password": PASSWORD, **overrides}

    def test_register_creates_user(self):
        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username=USERNAME)
        self.assertEqual(
            response.data, {"id": user.id, "username": USERNAME, "email": EMAIL}
        )
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.is_staff)

    def test_register_ignores_invalid_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user(USERNAME, "other@example.com", PASSWORD)

        for username in (USERNAME, USERNAME.upper()):
            with self.subTest(username=username):
                response = self.client.post(
                    self.url, self.payload(username=username)
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("username", response.data)

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user("other", EMAIL, PASSWORD)

        for email in (EMAIL, EMAIL.upper()):
            with self.subTest(email=email):
                response = self.client.post(self.url, self.payload(email=email))

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("email", response.data)

    def test_missing_fields_are_rejected(self):
        for field in ("username", "email", "password"):
            with self.subTest(field=field):
                payload = self.payload()
                del payload[field]

                response = self.client.post(self.url, payload)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_invalid_values_are_rejected(self):
        cases = [
            ("username", "has spaces"),
            ("username", "x" * 151),
            ("email", ""),
            ("email", "not-an-email"),
            ("password", "short"),
            ("password", "password123"),
            ("password", "1234567890"),
            ("password", USERNAME),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                response = self.client.post(self.url, self.payload(**{field: value}))

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)
                self.assertFalse(User.objects.exists())


class LoginTests(APITestCase):
    url = reverse("login")

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(USERNAME, EMAIL, PASSWORD)

    def test_login_returns_tokens(self):
        response = self.client.post(
            self.url, {"username": USERNAME, "password": PASSWORD}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_invalid_credentials_are_rejected(self):
        cases = [(USERNAME, "wrong-password"), ("unknown", PASSWORD)]
        for username, password in cases:
            with self.subTest(username=username):
                response = self.client.post(
                    self.url, {"username": username, "password": password}
                )

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertNotIn("access", response.data)

    def test_inactive_user_is_rejected(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        response = self.client.post(
            self.url, {"username": USERNAME, "password": PASSWORD}
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_password_is_rejected(self):
        response = self.client.post(self.url, {"username": USERNAME})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TokenRefreshTests(APITestCase):
    url = reverse("token-refresh")

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(USERNAME, EMAIL, PASSWORD)

    def test_refresh_returns_new_access_token(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(self.url, {"refresh": str(refresh)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_invalid_tokens_are_rejected(self):
        access = RefreshToken.for_user(self.user).access_token

        for token in ("not-a-token", str(access)):
            with self.subTest(token=token):
                response = self.client.post(self.url, {"refresh": token})

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_token_is_rejected(self):
        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProtectedView(APIView):
    """Sets no permissions, so it relies on the project defaults."""

    def get(self, request):
        return Response({"username": request.user.username})


urlpatterns = [path("protected/", ProtectedView.as_view())]


@override_settings(ROOT_URLCONF=__name__)
class DefaultPermissionTests(APITestCase):
    url = "/protected/"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(USERNAME, EMAIL, PASSWORD)

    def test_request_without_token_is_rejected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_request_with_invalid_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_request_with_refresh_token_is_rejected(self):
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh}")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_request_with_access_token_is_allowed(self):
        access = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"username": USERNAME})


class HealthCheckTests(APITestCase):
    def test_health_check_is_public(self):
        response = self.client.get(reverse("health-check"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "ok"})
