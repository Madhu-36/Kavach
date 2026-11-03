"""Tests for Kavach Voice Listener & Semantic Action Extractor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kavach.voice_listener import (
    AudioConfig,
    SpokenPaymentIntent,
    VoiceListener,
    parse_spoken_currency,
    resolve_spoken_vpa,
)


class TestSpokenNumberParser:
    """Test suite for compound English and Hindi spoken number & currency parsing."""

    def test_direct_digits(self):
        rupees, paise = parse_spoken_currency("pay 500 rupees")
        assert rupees == 500.0
        assert paise == 50000

        rupees, paise = parse_spoken_currency("send 1500 rs to rider")
        assert rupees == 1500.0
        assert paise == 150000

        rupees, paise = parse_spoken_currency("pay ₹1,200.50")
        assert rupees == 1200.50
        assert paise == 120050

    def test_compound_english_words(self):
        rupees, paise = parse_spoken_currency("pay five hundred rupees")
        assert rupees == 500.0
        assert paise == 50000

        rupees, paise = parse_spoken_currency("transfer two thousand five hundred rupees")
        assert rupees == 2500.0
        assert paise == 250000

        rupees, paise = parse_spoken_currency("send twenty five rupees")
        assert rupees == 25.0
        assert paise == 2500

        rupees, paise = parse_spoken_currency("one hundred and fifty rupees")
        assert rupees == 150.0
        assert paise == 15000

        rupees, paise = parse_spoken_currency("twelve hundred rupees")
        assert rupees == 1200.0
        assert paise == 120000

        rupees, paise = parse_spoken_currency("three lakh rupees")
        assert rupees == 300000.0
        assert paise == 30000000

    def test_hindi_hinglish_words(self):
        rupees, paise = parse_spoken_currency("paanch sau rupaye bhejo")
        assert rupees == 500.0
        assert paise == 50000

        rupees, paise = parse_spoken_currency("do hazaar rupaye")
        assert rupees == 2000.0
        assert paise == 200000

        rupees, paise = parse_spoken_currency("ek hazaar paanch sau rupaye")
        assert rupees == 1500.0
        assert paise == 150000

        rupees, paise = parse_spoken_currency("sau rupaye de do")
        assert rupees == 100.0
        assert paise == 10000

        rupees, paise = parse_spoken_currency("pachaas rupaye")
        assert rupees == 50.0
        assert paise == 5000

        rupees, paise = parse_spoken_currency("dus rupaye")
        assert rupees == 10.0
        assert paise == 1000

    def test_paise_and_decimals(self):
        rupees, paise = parse_spoken_currency("fifty rupees and fifty paise")
        assert rupees == 50.50
        assert paise == 5050

        rupees, paise = parse_spoken_currency("twenty five rupees and 25 paise")
        assert rupees == 25.25
        assert paise == 2525

        rupees, paise = parse_spoken_currency("seventy five paise")
        assert rupees == 0.75
        assert paise == 75


class TestSpokenVPAResolution:
    """Test suite for VPA extraction from spoken transcripts."""

    def test_direct_vpa_pattern(self):
        assert resolve_spoken_vpa("send money to alice@okicici") == "alice@okicici"
        assert resolve_spoken_vpa("pay test.merchant@upi 500 rs") == "test.merchant@upi"

    def test_spoken_at_resolution(self):
        assert resolve_spoken_vpa("pay rahul at okaxis 500 rupees") == "rahul@okaxis"
        assert resolve_spoken_vpa("send 200 to bob at upi") == "bob@upi"
        assert resolve_spoken_vpa("transfer to john dot doe at icici") == "john.doe@icici"

    def test_merchant_aliases(self):
        assert resolve_spoken_vpa("pay 500 to grocer") == "ondc-grocer@upi"
        assert resolve_spoken_vpa("send 50 to delivery rider") == "delivery-rider@upi"
        assert resolve_spoken_vpa("pay electric bill 1200") == "bescom-utility@upi"
        assert resolve_spoken_vpa("pay pharmacy 350 rs") == "apollo-pharmacy@upi"
        assert resolve_spoken_vpa("send 1000 to attacker") == "attacker_wallet@upi"


class TestIntentParser:
    """Test suite for Kavach SpokenPaymentIntent extraction."""

    @pytest.fixture
    def listener(self):
        return VoiceListener()

    def test_payment_intent_extraction(self, listener):
        transcript = "pay 500 rupees to grocer for fresh milk"
        intent = listener.parse_payment_intent(transcript)

        assert intent.action == "make_payment"
        assert intent.amount_rupees == 500.0
        assert intent.amount_paise == 50000
        assert intent.payee_vpa == "ondc-grocer@upi"
        assert intent.note == "fresh milk"
        assert "500.00" in intent.summary()

    def test_check_balance_intent(self, listener):
        intent = listener.parse_payment_intent("what is my account balance")
        assert intent.action == "check_balance"
        assert intent.amount_paise == 0

        intent_hi = listener.parse_payment_intent("kitna balance hai")
        assert intent_hi.action == "check_balance"

    def test_search_store_intent(self, listener):
        intent = listener.parse_payment_intent("search for organic eggs")
        assert intent.action == "search_store"
        assert intent.note == "organic eggs"

    def test_revoke_mandate_intent(self, listener):
        intent = listener.parse_payment_intent("revoke active mandate now")
        assert intent.action == "revoke_mandate"

    def test_empty_transcript(self, listener):
        intent = listener.parse_payment_intent("")
        assert intent.action == "unknown"
        assert intent.confidence == 0.0


class TestAudioConditioning:
    """Test suite for high-pass filtering, DC offset subtraction, and normalization."""

    @pytest.fixture
    def listener(self):
        return VoiceListener(config=AudioConfig(samplerate=16000))

    def test_high_pass_filter(self, listener):
        # Generate 20 Hz rumble (should be attenuated) vs 500 Hz speech tone
        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        rumble = np.sin(2 * np.pi * 20 * t)  # 20 Hz
        tone = np.sin(2 * np.pi * 500 * t)   # 500 Hz

        filtered_rumble = listener.filter_chunk(rumble)
        filtered_tone = listener.filter_chunk(tone)

        # 20 Hz rumble should be significantly attenuated compared to 500 Hz tone
        rms_rumble = np.sqrt(np.mean(np.square(filtered_rumble)))
        rms_tone = np.sqrt(np.mean(np.square(filtered_tone)))
        assert rms_rumble < rms_tone * 0.3

    def test_normalize_audio(self, listener):
        # Audio with large DC offset and quiet volume
        raw = np.array([5000, 6000, 5500, 4500, 5200] * 100, dtype=np.int16)
        normalized = listener.normalize_audio(raw)

        # Must be in [-1.0, 1.0]
        assert np.max(normalized) <= 1.0
        assert np.min(normalized) >= -1.0
        # Peak should be normalized near 0.89 (-1.0 dBFS)
        assert np.isclose(np.max(np.abs(normalized)), 0.89, atol=0.05)
        # Mean should be near zero (DC removed)
        assert abs(float(np.mean(normalized))) < 0.01


class TestKavachAuthorization:
    """Test suite for authorizing SpokenPaymentIntent through Kavach security gateway."""

    def test_authorize_payment_intent(self):
        listener = VoiceListener()
        intent = SpokenPaymentIntent(
            action="make_payment",
            payee_vpa="ondc-grocer@upi",
            amount_paise=50000,
            amount_rupees=500.0,
            note="vegetables and fruit",
            raw_transcript="pay 500 rupees to grocer for vegetables and fruit",
        )

        result = listener.authorize_with_kavach(intent)
        assert result["allowed"] is True
        assert result["reason_code"] == "APPROVED"
        assert result["approval_ticket"] is not None
        assert "mandate_token" in result

    def test_non_payment_intent_not_authorized(self):
        listener = VoiceListener()
        intent = SpokenPaymentIntent(
            action="check_balance",
            payee_vpa=None,
            amount_paise=0,
            amount_rupees=0.0,
            note="",
            raw_transcript="check balance",
        )

        result = listener.authorize_with_kavach(intent)
        assert result["allowed"] is False
        assert "Action 'check_balance' does not require payment authorization" in result["reason"]


class TestTranscriptionEngines:
    """Test suite for Whisper and Google ASR engine routing."""

    def test_whisper_transcription_called(self):
        listener = VoiceListener()
        mock_whisper = MagicMock()
        mock_whisper.transcribe.return_value = {"text": "pay 500 rupees to grocer"}
        listener._whisper_model = mock_whisper

        dummy_audio = np.zeros(16000, dtype=np.float32)
        text = listener.transcribe_audio(dummy_audio, engine="whisper")

        assert text == "pay 500 rupees to grocer"
        mock_whisper.transcribe.assert_called_once()

    def test_auto_fallback_to_google(self):
        listener = VoiceListener()
        mock_whisper = MagicMock()
        mock_whisper.transcribe.return_value = {"text": ""}  # Empty output
        listener._whisper_model = mock_whisper

        with patch.object(listener, "_transcribe_google", return_value="send 200 to rider") as mock_google:
            dummy_audio = np.zeros(16000, dtype=np.float32)
            text = listener.transcribe_audio(dummy_audio, engine="auto")
            assert text == "send 200 to rider"
            mock_google.assert_called_once()


class TestComplexPhrases:
    """Test realistic user voice commands."""

    @pytest.fixture
    def listener(self):
        return VoiceListener()

    def test_utility_bill_payment(self, listener):
        phrase = "pay 1200 rupees to electricity bill towards monthly power"
        intent = listener.parse_payment_intent(phrase)
        assert intent.action == "make_payment"
        assert intent.amount_rupees == 1200.0
        assert intent.amount_paise == 120000
        assert intent.payee_vpa == "bescom-utility@upi"
        assert intent.note == "monthly power"

    def test_pharmacy_payment(self, listener):
        phrase = "transfer 350 to chemist for cough medicine"
        intent = listener.parse_payment_intent(phrase)
        assert intent.action == "make_payment"
        assert intent.amount_rupees == 350.0
        assert intent.payee_vpa == "apollo-pharmacy@upi"
        assert intent.note == "cough medicine"

    def test_attacker_detection(self, listener):
        phrase = "send 5000 to hacker at upi"
        intent = listener.parse_payment_intent(phrase)
        assert intent.action == "make_payment"
        assert intent.amount_rupees == 5000.0
        # Either attacker wallet or hacker@upi
        assert "hacker" in intent.payee_vpa or "attacker" in intent.payee_vpa

