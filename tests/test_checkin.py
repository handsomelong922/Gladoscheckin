import unittest
from unittest.mock import Mock, patch

import checkin


class CheckinResponseTests(unittest.TestCase):
    def run_checkin(self, response_data, status_code=200):
        checkin_response = Mock(status_code=status_code, text=str(response_data))
        checkin_response.json.return_value = response_data
        state_response = Mock(status_code=200)
        state_response.json.return_value = {
            "data": {"email": "user@example.com", "leftDays": 12, "points": 50}
        }

        with patch.object(checkin.requests, "post", return_value=checkin_response), patch.object(
            checkin.requests, "get", return_value=state_response
        ):
            return checkin.perform_glados_checkin(
                "koa:sess=test",
                "https://glados.cloud/api/user/checkin",
                "https://glados.cloud/api/user/status",
                {"Content-Type": "application/json"},
                {"token": "glados.cloud"},
            )

    def test_permission_denied_is_failure_even_with_http_200(self):
        result, status = self.run_checkin({"code": 1, "message": "没有权限"})

        self.assertEqual(status, "fail")
        self.assertFalse(result["checkin_success"])
        self.assertIn("没有权限", result["message_status"])

    def test_repeat_message_is_classified_as_repeat(self):
        result, status = self.run_checkin(
            {"code": 1, "message": "Checkin Repeats! Please Try Tomorrow"}
        )

        self.assertEqual(status, "repeat")
        self.assertTrue(result["checkin_success"])

    def test_success_message_is_classified_as_success(self):
        result, status = self.run_checkin(
            {
                "code": 0,
                "message": "Checkin! Got 1 points",
                "list": [{"change": 1, "balance": 51}],
            }
        )

        self.assertEqual(status, "success")
        self.assertEqual(result["points_change"], 1)

    def test_unknown_http_200_response_is_failure(self):
        result, status = self.run_checkin({"code": 9, "message": "unexpected response"})

        self.assertEqual(status, "fail")
        self.assertFalse(result["checkin_success"])

    @patch("checkin.requests.post")
    def test_notification_sends_token_in_json_body(self, post):
        post.return_value = Mock(status_code=200, text='{"code":200}')
        post.return_value.json.return_value = {"code": 200, "msg": "执行成功"}

        checkin.send_wechat("secret-token", "title", "body")

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://www.pushplus.plus/send")
        self.assertEqual(kwargs["json"]["token"], "secret-token")
        self.assertNotIn("secret-token", args[0])

    @patch("checkin.requests.post")
    def test_notification_http_200_with_api_error_is_not_success(self, post):
        response = Mock(status_code=200, text='{"code":401,"msg":"token错误"}')
        response.json.return_value = {"code": 401, "msg": "token错误"}
        post.return_value = response

        self.assertIsNone(checkin.send_wechat("bad-token", "title", "body"))


if __name__ == "__main__":
    unittest.main()
