import requests
import json
import os
import time
import logging
import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def send_wechat(token, title, msg):
    """发送微信通知，添加重试机制和错误处理"""
    if not token:
        logger.warning("SENDKEY未设置，跳过通知发送")
        return None
        
    # 配置重试策略
    retry_strategy = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD", "TRACE", "OPTIONS"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    # 设置请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    template = 'html'
    url = f"https://www.pushplus.plus/send?token={token}&title={title}&content={msg}&template={template}"
    logger.info(f"发送通知URL: {url[:80]}...")
    print(url)
    
    # 主URL尝试
    for attempt in range(3):
        try:
            logger.info(f"尝试发送通知 (第{attempt + 1}次)")
            r = session.get(url=url, timeout=30, headers=headers, verify=True)
            logger.info(f"通知发送状态码: {r.status_code}")
            
            if r.status_code == 200:
                logger.info("通知发送成功")
                print(r.text)
                return r.text
            else:
                logger.warning(f"通知发送返回状态码: {r.status_code}")
                print(f"Response: {r.text}")
                
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL错误 (第{attempt + 1}次): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"连接错误 (第{attempt + 1}次): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
                
        except requests.exceptions.Timeout as e:
            logger.error(f"请求超时 (第{attempt + 1}次): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
                
        except Exception as e:
            logger.error(f"其他错误 (第{attempt + 1}次): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
    
    # 尝试备用域名
    backup_urls = [
        f"https://pushplus.hxtrip.com/send?token={token}&title={title}&content={msg}&template={template}",
        f"http://www.pushplus.plus/send?token={token}&title={title}&content={msg}&template={template}"
    ]
    
    for backup_url in backup_urls:
        try:
            logger.info(f"尝试备用URL: {backup_url[:80]}...")
            r = session.get(url=backup_url, timeout=20, headers=headers)
            if r.status_code == 200:
                logger.info("使用备用URL发送通知成功")
                print(r.text)
                return r.text
            else:
                print(f"Backup URL Response: {r.text}")
        except Exception as e:
            logger.error(f"备用URL失败: {e}")
            continue
    
    logger.error("所有通知发送方式都失败了")
    print("通知发送失败，但签到程序已完成")
    return None

def perform_glados_checkin(cookie, check_in_url, status_url, headers_template, payload):
    """执行单个账号的签到操作"""
    try:
        # 准备请求头
        headers = headers_template.copy()
        headers['cookie'] = cookie
        
        # 执行签到
        logger.info("开始执行签到...")
        checkin = requests.post(
            check_in_url, 
            headers=headers, 
            data=json.dumps(payload),
            timeout=30
        )
        
        # 获取账号状态
        logger.info("获取账号状态...")
        state = requests.get(
            status_url, 
            headers={k: v for k, v in headers.items() if k != 'content-type'},
            timeout=30
        )
        
        result = {
            'checkin_success': False,
            'status_success': False,
            'email': '',
            'points': 0,
            'leftdays': 0,
            'message_status': '未知错误',
            'check_result': '',
            'points_change': 0  # 新增积分变化字段
        }
        
        # 处理签到结果
        if checkin.status_code == 200:
            result['checkin_success'] = True
            try:
                checkin_data = checkin.json()
                result['check_result'] = checkin_data.get('message', '')
                result['points'] = checkin_data.get('points', 0)
                # 尝试提取积分变化
                if "Checkin! Got" in result['check_result']:
                    # 尝试从 "Checkin! Got 1 points." 这样的消息中提取数字
                    try:
                        points_str = result['check_result'].split("Got ")[1].split(" points")[0]
                        result['points_change'] = int(points_str)
                    except (IndexError, ValueError):
                        result['points_change'] = 0
                logger.info(f"签到响应: {result['check_result']}")
            except json.JSONDecodeError as e:
                logger.error(f"签到响应JSON解析失败: {e}")
                result['check_result'] = f"JSON解析失败: {checkin.text[:100]}"
        else:
            logger.error(f"签到请求失败，状态码: {checkin.status_code}")
            result['check_result'] = f"签到请求失败，状态码: {checkin.status_code}"
        
        # 处理状态查询结果
        if state.status_code == 200:
            result['status_success'] = True
            try:
                state_data = state.json()
                data = state_data.get('data', {})
                result['leftdays'] = int(float(data.get('leftDays', 0)))
                result['email'] = data.get('email', 'unknown')
                logger.info(f"账号: {result['email']}, 剩余天数: {result['leftdays']}")
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.error(f"状态响应解析失败: {e}")
                result['email'] = 'parse_error'
                result['leftdays'] = 0
        else:
            logger.error(f"状态查询失败，状态码: {state.status_code}")
            result['email'] = 'status_error'
            result['leftdays'] = 0
        
        # 判断签到结果
        if result['checkin_success']:
            check_result = result['check_result']
            if "Checkin! Got" in check_result:
                result['message_status'] = "签到成功，会员点数 + " + str(result['points_change'])
                return result, 'success'
            elif "Checkin Repeats!" in check_result:
                result['message_status'] = "重复签到，明天再来"
                return result, 'repeat'
            else:
                result['message_status'] = "签到失败，请检查..."
                return result, 'fail'
        else:
            result['message_status'] = "签到请求失败, 请检查..."
            return result, 'fail'
            
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
            'message_status': f'未知错误: {str(e)}',
            'check_result': str(e),
            'points_change': 0
        }, 'fail'

# -------------------------------------------------------------------------------------------
# github workflows
# -------------------------------------------------------------------------------------------
if __name__ == '__main__':
    logger.info("开始执行Glados签到脚本")
    
    # pushdeer key 申请地址 https://www.pushdeer.com/product.html
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
        cookies = [cookie.strip() for cookie in cookies if cookie.strip()]
    else:
        cookies = []

    if cookies:
        logger.info(f"找到 {len(cookies)} 个cookie")

        check_in_url = "https://glados.space/api/user/checkin"        # 签到地址
        status_url = "https://glados.space/api/user/status"          # 查看账户状态

        referer = 'https://glados.space/console/checkin'
        origin = "https://glados.space"
        useragent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
        
        headers_template = {
            'referer': referer,
            'origin': origin,
            'user-agent': useragent,
            'content-type': 'application/json;charset=UTF-8'
        }
        
        payload = {
            'token': 'glados.one'
        }
        
        account_results = []  # 存储每个账号的结果
        
        for i, cookie in enumerate(cookies):
            logger.info(f"处理第 {i+1}/{len(cookies)} 个账号")
            
            result, status = perform_glados_checkin(
                cookie, check_in_url, status_url, headers_template, payload
            )
            
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
            # 获取当前时间
            now = datetime.datetime.now()
            time_str = now.strftime("%Y/%m/%d %H:%M:%S")
            
            # 构建美化的通知内容
            account_context = f"--- 账号 {i+1} 签到结果 ---\n"
            
            if result['checkin_success']:
                points_change_str = f"+{result['points_change']}" if result['points_change'] > 0 else "0"
                account_context += f"积分变化: {points_change_str}\n"
                account_context += f"当前余额: {result['points']}\n"
            else:
                account_context += f"签到结果: {result['message_status']}\n"
                
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

    print("sckey:", sckey[:10] + "..." if len(sckey) > 10 else sckey)
    print("cookies:", [cookie[:20] + "..." if len(cookie) > 20 else cookie for cookie in cookies])
    
    # 推送消息
    # 未设置 sckey 则不进行推送
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
