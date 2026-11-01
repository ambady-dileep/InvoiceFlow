from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]
        extra_kwargs = {
            "username": {
                "validators": [
                    UnicodeUsernameValidator(),
                    UniqueValidator(
                        queryset=User.objects.all(),
                        lookup="iexact",
                        message="A user with that username already exists.",
                    ),
                ],
            },
            # The built-in User model does not require or enforce unique emails.
            "email": {
                "required": True,
                "allow_blank": False,
                "validators": [
                    UniqueValidator(
                        queryset=User.objects.all(),
                        lookup="iexact",
                        message="A user with that email already exists.",
                    ),
                ],
            },
            "password": {"write_only": True, "trim_whitespace": False},
        }

    def validate(self, attrs):
        user = User(username=attrs["username"], email=attrs["email"])
        try:
            validate_password(attrs["password"], user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": error.messages})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
