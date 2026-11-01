from rest_framework import generics
from rest_framework.permissions import AllowAny

from apps.accounts.serializers import RegisterSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    # A stale token in the request must not block a public endpoint.
    authentication_classes = []
    permission_classes = [AllowAny]
