import json
import logging
import os
import sys
import time
import datetime
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def send_wechat(token, title, msg):
    """Send a PushPlus notification without exposing its token in the URL."""
    if not token:
        logger.warning("SENDKEY未设置，跳过通知发送")
        return None

    payload = {
        "token": token,
        "title": title,
        "content": msg,
        "template": "html",
    }
    for attempt in range(3):
        try:
            response = requests.post(
                "https://www.pushplus.plus/send",
                json=payload,
                timeout=30,
            )
            logger.info("通知发送状态码: %s", response.status_code)
            if response.status_code == 200:
                try:
                    response_data = response.json()
                except ValueError:
                    response_data = {}
                if isinstance(response_data, dict) and response_data.get("code") == 200:
                    logger.info("通知发送成功")
                    return response.text
                logger.warning(
                    "通知服务返回失败: %s",
                    response_data.get("msg", "无法识别的响应"),
                )
                return None
            logger.warning("通知发送返回状态码: %s", response.status_code)
        except requests.exceptions.RequestException as error:
            logger.error("通知发送失败 (第%s次): %s", attempt + 1, error)
        if attempt < 2:
            time.sleep(2 ** attempt)

    logger.error("所有通知发送方式都失败了")
    return None


def classify_checkin_response(checkin_data):
    """Classify a GLaDOS response by its message, not its HTTP status alone."""
    message = checkin_data.get("message", "")
    if not isinstance(message, str):
        message = str(message)
    message = message.casefold()

    if any(marker in message for marker in (
        "没有权限", "无权限", "unauthorized", "permission denied", "forbidden"
    )):
        return "fail"
    if "checkin repeats!" in message or "重复签到" in message:
        return "repeat"
    if "checkin! got" in message:
        return "success"
    return "fail"

def perform_glados_checkin(cookie, check_in_url, status_url, headers_template, payload):
    """执行单个账号的签到操作"""
    try:
        headers = headers_template.copy()
        headers['cookie'] = cookie

        logger.info("开始执行签到...")
        checkin = requests.post(
            check_in_url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        logger.info("获取账号状态...")
        state = requests.get(
            status_url,
            headers={key: value for key, value in headers.items()
                     if key.lower() != "content-type"},
            timeout=30,
        )

        result = {
            "checkin_success": False,
            "status_success": False,
            "email": "",
            "points": 0,
            "leftdays": 0,
            "message_status": "未知错误",
            "check_result": "",
            "points_change": 0,
        }

        checkin_status = "fail"
        if checkin.status_code == 200:
            try:
                checkin_data = checkin.json()
                if not isinstance(checkin_data, dict):
                    raise ValueError("签到响应不是JSON对象")
                result["check_result"] = str(checkin_data.get("message", ""))
                checkin_status = classify_checkin_response(checkin_data)

                entries = checkin_data.get("list", [])
                if entries and isinstance(entries[0], dict):
                    result["points_change"] = int(float(entries[0].get("change", 0)))
                    result["points"] = int(float(entries[0].get("balance", 0)))
                elif checkin_status == "success":
                    try:
                        result["points_change"] = int(
                            result["check_result"].split("Got ", 1)[1].split(" ", 1)[0]
                        )
                    except (IndexError, ValueError):
                        result["points_change"] = 1
                logger.info("签到响应: %s", result["check_result"])
            except (json.JSONDecodeError, ValueError, TypeError, IndexError) as error:
                logger.error("签到响应解析失败: %s", error)
                result["check_result"] = f"签到响应解析失败: {checkin.text[:100]}"
        else:
            logger.error(f"签到请求失败，状态码: {checkin.status_code}")
            result["check_result"] = f"签到请求失败，状态码: {checkin.status_code}"

        if state.status_code == 200:
            try:
                state_data = state.json()
                data = state_data.get("data", {})
                if not isinstance(data, dict):
                    raise ValueError("状态响应 data 字段无效")
                result["leftdays"] = int(float(data.get("leftDays", 0)))
                result["email"] = data.get("email", "unknown")
                if result["points"] == 0:
                    result["points"] = int(float(data.get("points", 0)))
                result["status_success"] = True
                logger.info("账号: %s, 剩余天数: %s", result["email"], result["leftdays"])
            except (json.JSONDecodeError, ValueError, TypeError) as error:
                logger.error("状态响应解析失败: %s", error)
                result["email"] = "parse_error"
        else:
            logger.error(f"状态查询失败，状态码: {state.status_code}")
            result["email"] = "status_error"

        result["checkin_success"] = checkin_status in ("success", "repeat")
        if checkin_status == "success":
            result["message_status"] = (
                "签到成功，会员点数 + " + str(result["points_change"])
            )
        elif checkin_status == "repeat":
            result["message_status"] = "重复签到，明天再来"
        else:
            detail = result["check_result"] or "签到响应无法识别"
            result["message_status"] = "签到失败: " + detail[:50]
            logger.warning("签到未成功: %s", detail)
        return result, checkin_status

    except requests.exceptions.Timeout as e:
        logger.error(f"请求超时: {e}")
        return {
            'checkin_success': False,
            'status_success': False,
            'email': 'timeout_error',
            'points': 0,
            'leftdays': 0,
            'message_status': '请求超时',
            'check_result': str(e),
            'points_change': 0
        }, 'fail'
    except requests.exceptions.ConnectionError as e:
        logger.error(f"连接错误: {e}")
        return {
            'checkin_success': False,
            'status_success': False,
            'email': 'connection_error',
            'points': 0,
            'leftdays': 0,
            'message_status': '连接失败',
            'check_result': str(e),
            'points_change': 0
        }, 'fail'
    except Exception as e: 
        logger.error(f"签到过程中出现未知错误: {e}")
        return {
            'checkin_success': False,
            'status_success': False,
            'email': 'unknown_error',
            'points': 0,
            'leftdays': 0,
            'message_status':  f'未知错误: {str(e)}',
            'check_result': str(e),
            'points_change': 0
        }, 'fail'

def get_beijing_time():
    """获取北京时间（UTC+8）"""
    # 获取UTC时间并加8小时转换为北京时间
    utc_now = datetime.datetime.utcnow()
    beijing_time = utc_now + datetime. timedelta(hours=8)
    return beijing_time.strftime("%Y/%m/%d %H:%M:%S")

# -------------------------------------------------------------------------------------------
# github workflows
# -------------------------------------------------------------------------------------------
if __name__ == '__main__':
    logger.info("开始执行Glados签到脚本")
    
    # pushdeer key 申请地址 https://www.pushdeer.com/product. html
    sckey = os.environ.get("SENDKEY", "")

    # 推送内容
    title = ""
    success, fail, repeats = 0, 0, 0        # 成功账号数量 失败账号数量 重复签到账号数量
    context = ""

    # glados账号cookie 直接使用数组 如果使用环境变量需要字符串分割一下
    cookies_env = os.environ.get("COOKIES", "")
    if cookies_env:
        cookies = cookies_env.split("&")
        # 过滤空字符串
        cookies = [cookie. strip() for cookie in cookies if cookie.strip()]
    else:
        cookies = []

    if cookies:
        logger.info(f"找到 {len(cookies)} 个cookie")

        # 只使用 glados.cloud 端点
        api_endpoints = [
            {
                'checkin':  'https://glados.cloud/api/user/checkin',
                'status': 'https://glados.cloud/api/user/status',
                'origin': 'https://glados.cloud'
            }
        ]

        useragent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
        
        # Token 改为 glados.cloud
        payload = {
            'token': 'glados.cloud'
        }
        
        account_results = []  # 存储每个账号的结果
        
        for i, cookie in enumerate(cookies):
            logger.info(f"处理第 {i+1}/{len(cookies)} 个账号")
            
            # 尝试API端点
            result = None
            for endpoint in api_endpoints:
                try:
                    # 移除 referer 请求头
                    headers_template = {
                        'Accept': 'application/json, text/plain, */*',
                        'Content-Type': 'application/json;charset=UTF-8',
                        'Origin': endpoint['origin'],
                        'User-Agent': useragent
                    }
                    
                    logger.info(f"尝试使用API端点: {endpoint['checkin']}")
                    result, status = perform_glados_checkin(
                        cookie, endpoint['checkin'], endpoint['status'], headers_template, payload
                    )
                    
                    if status in ('success', 'repeat'):
                        logger.info(f"签到完成，使用API端点: {endpoint['checkin']}")
                        break
                    logger.warning(f"签到端点返回失败: {result['check_result']}")
                        
                except Exception as e: 
                    logger.error(f"❌ API端点异常: {e}")
                    continue
            
            if result is None:
                result = {
                    'checkin_success': False,
                    'status_success': False,
                    'email': 'all_failed',
                    'points': 0,
                    'leftdays': 0,
                    'message_status': '签到失败',
                    'check_result': '签到失败',
                    'points_change': 0
                }
                status = 'fail'
            
            # 统计结果
            if status == 'success':
                success += 1
            elif status == 'repeat':
                repeats += 1
            else:
                fail += 1
            
            # 存储结果
            account_results.append(result)
            
            print(result['check_result'])
            
            # 设置标题（最后一个账号的状态作为标题）
            title = result['message_status']
            
            # 避免请求过于频繁
            if i < len(cookies) - 1:
                time.sleep(1)

        # 格式化通知内容
        for i, result in enumerate(account_results):
            # 获取北京时间
            time_str = get_beijing_time()
            
            # 构建美化的通知内容
            account_context = f"--- 账号 {i+1} 签到结果 ---\n"
            
            if result['checkin_success']: 
                if result['points_change'] > 0:
                    # 成功签到
                    account_context += f"积分变化: +{result['points_change']}\n"
                    account_context += f"当前余额: {result['points']}\n"
                elif "Checkin Repeats!" in result['check_result']:
                    # 重复签到
                    account_context += f"积分变化: +{result['points_change']} (重复签到)\n"
                    account_context += f"当前余额: {result['points']}\n"
                else:
                    # 其他情况
                    account_context += f"签到结果: {result['message_status']}\n"
                    account_context += f"当前余额: {result['points']}\n"
            else: 
                # 签到失败
                account_context += f"签到结果:  {result['message_status']}\n"
                if result['status_success']:
                    account_context += f"当前余额: {result['points']}\n"
                
            if result['status_success']:
                account_context += f"剩余天数: {result['leftdays']}天\n"
            else: 
                account_context += "剩余天数: 获取失败\n"
                
            account_context += f"签到时间: {time_str}\n"
            
            # 添加分隔符
            if i < len(account_results) - 1:
                account_context += "\n"
                
            context += account_context

        # 推送内容
        if len(cookies) > 1:
            title = f'Glados签到完成, 成功{success},失败{fail},重复{repeats}'
        
        logger.info(f"签到完成: 成功{success}, 失败{fail}, 重复{repeats}")
        print("Send Content:" + "\n", context)
        
    else:
        # 推送内容
        title = '# 未找到 cookies!'
        context = '请检查COOKIES环境变量是否正确设置'
        logger.error("未找到有效的cookies")

    # 推送消息
    if not sckey:
        print("Not push")
        logger.info("未设置SENDKEY，跳过推送")
    else:
        logger.info("开始发送通知")
        try:
            send_wechat(sckey, title, context)
        except Exception as e:
            logger.error(f"发送通知时出现异常: {e}")
            print(f"通知发送异常: {e}")

    logger.info("脚本执行完成")
    if fail or not cookies:
        sys.exit(1)
