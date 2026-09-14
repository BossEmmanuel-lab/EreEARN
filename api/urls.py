"""
URL configuration for the ÉréEARN API app.
"""

from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

app_name = "api"

urlpatterns = [
    # Documentation
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema"), name="redoc"),

    # Authentication
    path("auth/register/", views.UserRegisterView.as_view(), name="user-register"),
    path("auth/login/", views.UserLoginView.as_view(), name="user-login"),
    path("auth/challenge/", views.WalletChallengeView.as_view(), name="wallet-challenge"),
    path("auth/verify/", views.WalletVerifyView.as_view(), name="wallet-verify"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", views.UserProfileView.as_view(), name="user-profile"),

    # Bounties
    path("bounties/", views.BountyListView.as_view(), name="bounty-list"),
    path("bounties/prepare-fund/", views.BountyPrepareFundView.as_view(), name="bounty-prepare-fund"),
    path("bounties/create/", views.BountyCreateView.as_view(), name="bounty-create"),
    path("bounties/<uuid:pk>/", views.BountyDetailView.as_view(), name="bounty-detail"),
    path("bounties/<uuid:pk>/claim/", views.BountyClaimView.as_view(), name="bounty-claim"),
    path("bounties/<uuid:pk>/submit/", views.SubmissionCreateView.as_view(), name="bounty-submit"),
    path("bounties/<uuid:pk>/review/", views.SubmissionReviewView.as_view(), name="bounty-review"),
    path("bounties/<uuid:pk>/expire/", views.BountyExpireView.as_view(), name="bounty-expire"),

    # Dashboards
    path("dashboard/contributor/", views.ContributorDashboardView.as_view(), name="dashboard-contributor"),
    path("dashboard/poster/", views.PosterDashboardView.as_view(), name="dashboard-poster"),

    # Transactions
    path("transactions/<uuid:bounty_id>/", views.TransactionListView.as_view(), name="transaction-list"),
]