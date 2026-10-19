# 手机号注册与密码登录

登录页提供手机号登录、注册账号和原有访问令牌入口。注册页：`http://127.0.0.1:5173/login?mode=register`。支持中国大陆11位手机号及`+86`格式，统一保存为`+86`加11位号码。

手机号在本版本中只是账号标识，**没有接短信服务、未验证号码归属**。注册响应始终`phone_verified=false`；不能用于实名认证、已验证联系方式或密码找回。页面明确说明这一点，没有假验证码或假短信发送结果。

## 用户与权限

- 注册成功直接获得普通用户会话；手机号密码可以再次登录同一个账号。
- 密码要求15—128个Unicode字符，采用NFC规范化；不强制字符组合，拦截有限常见口令、连续重复字符和包含本手机号的口令。没有声称接入完整泄露密码库。
- 密码使用随机16字节盐和scrypt（N=2^17、r=8、p=1）保存；数据库不保存明文密码。每个API进程最多并行两份密码计算。
- 会话沿用既有AccessToken，数据库只存令牌SHA-256；默认8小时，单账号最多10个活动会话，超出后撤销最旧会话。
- 普通账号不能签发管理令牌或调用管理员接口；账号停用、令牌到期/撤销后无法访问私有会话。原有管理员签发的普通用户令牌仍兼容。
- 手机号不作为会话owner，owner为账号UUID的稳定标识；用户之间的问答历史按既有归属校验隔离。

客户端凭据只在内存中，手机号/密码/令牌不写入localStorage、日志或URL。手动账号授权后禁用自动本地会话替代；刷新页面需重新登录，不能悄悄切回开发机旧用户。仅保存一个非敏感的手动登录偏好。旧会话缓存按令牌摘要隔离，账号登录时清除未按账号隔离的本地问题草稿。

密码规则参考[NIST SP800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html)，scrypt成本参考[OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。这不表示整个系统已经取得标准合规认证。

## API（前缀/api/v1）

| 接口 | 权限与行为 |
|---|---|
| POST /auth/register | JSON为phone/password；201返回普通用户会话、账号ID、掩码手机号、到期时间及phone_verified=false |
| POST /auth/login | JSON为phone/password；200返回新的普通用户会话，手机号不存在或密码错误均为401 invalid_credentials |
| GET /auth/me | Bearer鉴权，验证当前普通用户；不返回手机号、密码或模型凭据 |
| POST /auth/logout | Bearer鉴权，只撤销当前令牌，不撤销其他用户或本人的其他令牌 |
| POST /auth/tokens、DELETE /auth/tokens/{id} | 保持管理员鉴权，不向公众开放 |

成功返回的登录凭据设`Cache-Control: no-store`。失败仍使用统一error.code/request_id，不回显密码或请求体。重复注册409 `registration_unavailable`，弱密码422 `weak_password`；429返回Retry-After，Redis限流服务不可用时503，不能绕过限流继续注册。

使用既有Redis数据库2、独立命名空间（含后端数据库名）计数。注册：同一实际客户端IP每小时5次、同一手机号每小时3次；登录：IP每分钟30次、手机号每分钟10次。号码和IP只用HMAC摘要作为Redis键，不信任调用者传入的X-Forwarded-For。经反向代理部署时应按实际拓扑补充可信代理配置，不任意放开限流。

## 部署与验证

0012增量迁移新增user_accounts及AccessToken可空account_id关联。既有用户令牌和问答记录不改写，数据库卷保留。不提供会删除账号数据的自动降级。

配置`SIGNUP_ENABLED=false`可以关闭新注册，已有账号仍可登录；`ACCOUNT_SESSION_HOURS`控制会话时长（1—24，默认8）。本机使用回环HTTP进行开发；正式部署账号接口应通过HTTPS提供。

```powershell
docker compose up -d --build --wait
uv run python scripts/ci-check.py --new-db --check-contracts
uv run python scripts/verify-phone-accounts.py
```

HTTP核验脚本创建新的verify_accounts_*数据库及临时回环API，使用明确虚构的账号，验证注册、重复注册、登录、身份、权限、会话隔离、退出及API重启后账号保留。临时API随后停止，不在业务库创建测试账号，不调用短信或模型。验证数据及日志只留本地output/accounts目录，不上传。

本机2026-10-15验证：源工程后端217项通过，前端29项通过，类型检查和构建通过；运行中的业务API已提供register/login/me/logout路由。专用数据库中的15项真实HTTP核验通过，结果以本地output/accounts/*/result.json为准。没有替用户注册真实手机号或设置密码；用户在页面自行填写。

团队合并版在Windows上390项通过、2项失败；新增账号测试全部通过，迁移与契约无漂移、212个公开候选文件扫描无发现。失败为已有的POSIX私有密钥文件权限及os.O_NONBLOCK在Windows上的兼容问题，未放宽测试。Linux结果以对应提交的GitHub CI为准，不能用此前提交的成功替代本次验证。
