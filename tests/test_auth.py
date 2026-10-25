import unittest
from unittest.mock import Mock, patch

from memeaward.api import ApiError, Twitch


class TwitchAuthTests(unittest.TestCase):
    @patch('memeaward.api.request')
    def test_expired_token_refreshes_rotates_and_retries(self, request):
        saved = Mock()
        twitch = Twitch('client', 'old', 'refresh', saved)
        request.side_effect = [ApiError('Twitch', 401),
                               {'access_token': 'new', 'refresh_token': 'rotated'},
                               {'data': []}]
        twitch.enable('reward', False)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[1].args[3], {
            'client_id': 'client', 'grant_type': 'refresh_token', 'refresh_token': 'refresh'})
        self.assertEqual(request.call_args.args[4]['Authorization'], 'Bearer new')
        saved.assert_called_once_with('new', 'rotated')
        self.assertEqual(twitch.refresh_token, 'rotated')

    @patch('memeaward.api.request')
    def test_validation_can_restore_expired_saved_session(self, request):
        request.side_effect = [ApiError('Twitch', 401),
                               {'access_token': 'new', 'refresh_token': 'rotated'},
                               {'client_id': 'client', 'scopes': Twitch.SCOPE.split(),
                                'user_id': 'user', 'login': 'login'}]
        twitch = Twitch('client', 'old', 'refresh')
        self.assertEqual(twitch.validate(), 'login')
        self.assertEqual(request.call_args.args[4]['Authorization'], 'OAuth new')

    @patch('memeaward.api.request')
    def test_does_not_retry_network_errors_or_other_statuses(self, request):
        for status in (0, 403, 429, 500):
            request.reset_mock()
            request.side_effect = ApiError('Twitch', status)
            with self.assertRaises(ApiError):
                Twitch('client', 'old', 'refresh').enable('reward', True)
            self.assertEqual(request.call_count, 1)

    @patch('memeaward.api.request')
    def test_legacy_token_requires_login(self, request):
        request.side_effect = ApiError('Twitch', 401)
        with self.assertRaises(ApiError):
            Twitch('client', 'old').validate()
        self.assertEqual(request.call_count, 1)

    @patch('memeaward.api.request')
    def test_retry_is_bounded(self, request):
        request.side_effect = [ApiError('Twitch', 401),
                               {'access_token': 'new', 'refresh_token': 'rotated'},
                               ApiError('Twitch', 401)]
        with self.assertRaises(ApiError):
            Twitch('client', 'old', 'refresh').validate()
        self.assertEqual(request.call_count, 3)

    @patch('memeaward.api.request')
    def test_revoked_refresh_token_stops(self, request):
        request.side_effect = [ApiError('Twitch', 401), ApiError('Twitch', 401)]
        with self.assertRaises(ApiError):
            Twitch('client', 'old', 'refresh').validate()
        self.assertEqual(request.call_count, 2)

    @patch('memeaward.api.request')
    def test_chat_uses_refresh(self, request):
        request.side_effect = [ApiError('Twitch', 401),
                               {'access_token': 'new', 'refresh_token': 'rotated'},
                               {'data': [{'is_sent': True}]}]
        Twitch('client', 'old', 'refresh').notify_not_supporter('viewer')
        self.assertEqual(request.call_args.args[4]['Authorization'], 'Bearer new')
