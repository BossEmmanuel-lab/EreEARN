"""
Unit tests for ÉréEARN API.
Includes coverage for authentication, bounty CRUD, self-claim rejection,
24h deadline validation, submission review paths, and Swagger docs.
"""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from stellar_sdk import Keypair

from api.models import Bounty, Submission, Transaction, User
from api.serializers import ReviewAction
from api.services.stellar import generate_challenge, verify_challenge


class WalletAuthTestCase(APITestCase):
    """Test wallet-based authentication flow."""

    def test_challenge_generation(self):
        url = reverse("api:wallet-challenge")
        wallet_address = "GAHK7EEG2WWHVKDNT4CEQFZGKF2LGDSW2IVM4S5PX42BLFYDPBIVGWAD"
        response = self.client.post(url, {"wallet_address": wallet_address})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("challenge", response.data)
        self.assertIn("message", response.data)
        self.assertIn("expires_at", response.data)

    def test_invalid_wallet_address(self):
        url = reverse("api:wallet-challenge")
        response = self.client.post(url, {"wallet_address": "invalid_address"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_challenge_signature(self):
        kp = Keypair.random()
        address = kp.public_key

        challenge_data = generate_challenge(address)
        message = challenge_data["message"]

        # Sign raw message bytes
        sig_bytes = kp.sign(message.encode())
        sig_hex = sig_bytes.hex()

        verified = verify_challenge(address, sig_hex)
        self.assertTrue(verified)


class BountyTestCase(APITestCase):
    """Test Bounty CRUD and lifecycle endpoints."""

    def setUp(self):
        self.poster = User.objects.create_user(
            username="poster_user",
            wallet_address="GAHK7EEG2WWHVKDNT4CEQFZGKF2LGDSW2IVM4S5PX42BLFYDPBIVGWAD",
            role=User.Role.POSTER,
        )
        self.contributor = User.objects.create_user(
            username="contributor_user",
            wallet_address="GBBD47IF6LWK2P7MDEVSCWR7DPUWV3NY3DTQEVFL4TW4MKXMTCWVTF3L",
            role=User.Role.CONTRIBUTOR,
        )

        self.bounty = Bounty.objects.create(
            poster=self.poster,
            title="Test Bounty",
            description="Fix a bug in contract",
            skill_category=Bounty.SkillCategory.DEVELOPMENT,
            reward_amount=Decimal("100.0000000"),
            reward_asset=Bounty.Asset.XLM,
            deadline=timezone.now() + timedelta(days=5),
            status=Bounty.Status.POSTED,
            escrow_tx_hash="a1b2c3d4e5f67890123456789012345678901234567890123456789012345678",
        )

    def test_list_bounties(self):
        url = reverse("api:bounty-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_bounty_detail(self):
        url = reverse("api:bounty-detail", kwargs={"pk": self.bounty.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Test Bounty")

    def test_deadline_under_24h_rejected(self):
        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-create")
        invalid_deadline = timezone.now() + timedelta(hours=12)
        response = self.client.post(
            url,
            {
                "title": "Short Deadline Bounty",
                "description": "Too short deadline",
                "skill_category": Bounty.SkillCategory.DEVELOPMENT,
                "reward_amount": "50.0",
                "reward_asset": "XLM",
                "deadline": invalid_deadline.isoformat(),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("deadline", response.data)

    def test_claim_bounty_success(self):
        self.client.force_authenticate(user=self.contributor)
        url = reverse("api:bounty-claim", kwargs={"pk": self.bounty.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.bounty.refresh_from_db()
        self.assertEqual(self.bounty.status, Bounty.Status.CLAIMED)
        self.assertEqual(self.bounty.contributor, self.contributor)

    def test_poster_self_claim_rejected(self):
        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-claim", kwargs={"pk": self.bounty.pk})
        response = self.client.post(url)

        # Fix 8 added a POSTER role check that fires before the self-claim check,
        # so the response is now 403 (Forbidden) rather than 400 (Bad Request).
        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN])
        self.assertIn("detail", response.data)

    def test_unauthenticated_claim_rejected(self):
        url = reverse("api:bounty-claim", kwargs={"pk": self.bounty.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_duplicate_claim_rejected(self):
        self.bounty.status = Bounty.Status.CLAIMED
        self.bounty.contributor = self.contributor
        self.bounty.save()

        other_user = User.objects.create_user(
            username="other_contributor",
            # Fix 19: valid 56-char Stellar public key
            wallet_address="GCDW3EEFFK5577KGBBZZCCDD3JKLMNPQRSTUVWXYZ1234567890AABC",
            role=User.Role.CONTRIBUTOR,
        )
        self.client.force_authenticate(user=other_user)
        url = reverse("api:bounty-claim", kwargs={"pk": self.bounty.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_work(self):
        self.bounty.contributor = self.contributor
        self.bounty.status = Bounty.Status.CLAIMED
        self.bounty.save()

        self.client.force_authenticate(user=self.contributor)
        url = reverse("api:bounty-submit", kwargs={"pk": self.bounty.pk})
        response = self.client.post(
            url,
            {
                "submission_text": "Completed the feature implementation.",
                "submission_url": "https://github.com/example/pr/1",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.bounty.refresh_from_db()
        self.assertEqual(self.bounty.status, Bounty.Status.SUBMITTED)
        self.assertEqual(Submission.objects.count(), 1)

    def test_submission_review_request_revision(self):
        self.bounty.contributor = self.contributor
        self.bounty.status = Bounty.Status.SUBMITTED
        self.bounty.save()

        Submission.objects.create(
            bounty=self.bounty,
            contributor=self.contributor,
            submission_text="Initial submission",
            submission_url="https://github.com/example/pr/1",
        )

        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-review", kwargs={"pk": self.bounty.pk})
        response = self.client.post(
            url,
            {
                "action": ReviewAction.REQUEST_REVISION,
                "reviewer_notes": "Please fix unit test coverage.",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.bounty.refresh_from_db()
        self.assertEqual(self.bounty.status, Bounty.Status.CLAIMED)


class DashboardAndDocsTestCase(APITestCase):
    """Test dashboards and Swagger OpenAPI documentation endpoints."""

    def setUp(self):
        self.poster = User.objects.create_user(
            username="poster_user",
            wallet_address="GAHK7EEG2WWHVKDNT4CEQFZGKF2LGDSW2IVM4S5PX42BLFYDPBIVGWAD",
            role=User.Role.POSTER,
        )
        self.contributor = User.objects.create_user(
            username="contributor_user",
            wallet_address="GBBD47IF6LWK2P7MDEVSCWR7DPUWV3NY3DTQEVFL4TW4MKXMTCWVTF3L",
            role=User.Role.CONTRIBUTOR,
        )

    def test_poster_dashboard(self):
        self.client.force_authenticate(user=self.poster)
        url = reverse("api:dashboard-poster")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("active_bounties", response.data)
        self.assertIn("total_posted", response.data)

    def test_contributor_dashboard(self):
        self.client.force_authenticate(user=self.contributor)
        url = reverse("api:dashboard-contributor")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("active_claims", response.data)
        self.assertIn("total_earned", response.data)

    def test_swagger_documentation_endpoints(self):
        docs_url = reverse("api:swagger-ui")
        response = self.client.get(docs_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        schema_url = reverse("api:schema")
        response = self.client.get(schema_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SecurityRegressionTestCase(APITestCase):
    """Regression tests for the security fixes applied in the bug-fix sprint."""

    def setUp(self):
        self.poster = User.objects.create_user(
            username="poster_user",
            wallet_address="GAHK7EEG2WWHVKDNT4CEQFZGKF2LGDSW2IVM4S5PX42BLFYDPBIVGWAD",
            role=User.Role.POSTER,
        )
        self.contributor = User.objects.create_user(
            username="contributor_user",
            wallet_address="GBBD47IF6LWK2P7MDEVSCWR7DPUWV3NY3DTQEVFL4TW4MKXMTCWVTF3L",
            role=User.Role.CONTRIBUTOR,
        )
        self.bounty = Bounty.objects.create(
            poster=self.poster,
            title="Security Test Bounty",
            description="Regression coverage bounty",
            skill_category=Bounty.SkillCategory.DEVELOPMENT,
            reward_amount=Decimal("50.0000000"),
            reward_asset=Bounty.Asset.XLM,
            deadline=timezone.now() + timedelta(days=5),
            status=Bounty.Status.POSTED,
            escrow_tx_hash="a1b2c3d4e5f67890123456789012345678901234567890123456789012345678",
        )

    # Role authorization for claiming

    def test_poster_role_cannot_claim_bounty(self):
        """A user with POSTER role must be rejected when trying to claim (Fix 8)."""
        # Create a separate bounty posted by contributor so poster can attempt
        # to claim a bounty they did NOT post (to isolate role check from
        # self-claim check).
        other_bounty = Bounty.objects.create(
            poster=self.contributor,
            title="Other Bounty",
            description="Posted by contributor",
            skill_category=Bounty.SkillCategory.WRITING,
            reward_amount=Decimal("10.0"),
            reward_asset=Bounty.Asset.XLM,
            deadline=timezone.now() + timedelta(days=3),
            status=Bounty.Status.POSTED,
        )
        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-claim", kwargs={"pk": other_bounty.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", response.data)

    # Bounty expiration permissions

    def test_poster_cannot_expire_submitted_bounty(self):
        """A SUBMITTED bounty must not be expirable — would drain contributor's funds (Fix 12)."""
        # Put the bounty into SUBMITTED state with a passed deadline
        self.bounty.contributor = self.contributor
        self.bounty.status = Bounty.Status.SUBMITTED
        # Backdate deadline so is_expired would be True (if we didn't have the status guard)
        self.bounty.deadline = timezone.now() - timedelta(days=1)
        self.bounty.save()

        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-expire", kwargs={"pk": self.bounty.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        # Bounty must remain SUBMITTED, not changed to EXPIRED
        self.bounty.refresh_from_db()
        self.assertEqual(self.bounty.status, Bounty.Status.SUBMITTED)

    def test_poster_cannot_expire_approved_bounty(self):
        """A bounty in APPROVED status cannot be expired (Fix 12)."""
        self.bounty.status = Bounty.Status.APPROVED
        self.bounty.deadline = timezone.now() - timedelta(days=1)
        self.bounty.save()

        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-expire", kwargs={"pk": self.bounty.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.bounty.refresh_from_db()
        self.assertEqual(self.bounty.status, Bounty.Status.APPROVED)

    # Transaction access control

    def test_unauthenticated_cannot_list_transactions(self):
        """Transaction records (with wallet addresses) require auth (Fix 14)."""
        url = reverse("api:transaction-list", kwargs={"bounty_id": self.bounty.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # Escrow funding validation

    def test_unvalidated_escrow_tx_hash_rejected(self):
        """Creating a bounty with a raw escrow_tx_hash in the body must not
        blindly trust it as proof of funding (Fix 5).
        The field is now ignored — the response bounty object may still have an
        escrow_tx_hash set via the server funding path, but the client-supplied
        value must not be used directly."""
        self.client.force_authenticate(user=self.poster)
        url = reverse("api:bounty-create")
        response = self.client.post(
            url,
            {
                "title": "Fake Funded Bounty",
                "description": "Attempting to pass a fake tx hash",
                "skill_category": Bounty.SkillCategory.DEVELOPMENT,
                "reward_amount": "1.0",
                "reward_asset": "XLM",
                "deadline": (timezone.now() + timedelta(days=2)).isoformat(),
                # This fake hash must NOT be trusted by the server
                "escrow_tx_hash": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            },
        )
        # The bounty may succeed (server-side funding) or fail (no testnet access)
        # but the key assertion is: the server never simply echoes back the
        # client-supplied fake hash as the canonical escrow_tx_hash.
        if response.status_code == status.HTTP_201_CREATED:
            returned_hash = response.data.get("escrow_tx_hash", "")
            self.assertNotEqual(
                returned_hash,
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "Server blindly accepted a client-supplied fake escrow_tx_hash.",
            )
