
# Gwofy Guard Service（Shopify 公开应用 + AWS）

基于 Python CDK 的应用，部署 **DynamoDB**、**KMS**、**SQS** 与 **HTTP API**，Lambda 负责 OAuth、Shopify Webhook（仅入队）、异步 Worker、会话令牌 API 与定时对账。

## 本机与 CI 凭证配置（详细步骤）

`app.py` 在 **合成（synth）和部署（deploy）时** 读取 **进程环境变量**（以及 CDK `-c` context）。下面说明如何在「本机」和「CI」里可靠地注入变量。**请勿把真实密钥提交到 Git**；仓库已忽略 `.env.local`（见 `.gitignore`）。

### 本机（推荐顺序）

**1）AWS 账号访问（二选一）**

- **推荐：`aws configure`（长期密钥）**  
  ```bash
  aws configure --profile gwofy-hk
  ```  
  按提示填入：`AWS Access Key ID`、`AWS Secret Access Key`、默认区域（例如 `ap-east-1`）、输出格式（可填 `json`）。  
  之后在本终端使用：  
  ```bash
  export AWS_PROFILE=gwofy-hk
  export AWS_REGION=ap-east-1
  export AWS_DEFAULT_REGION=ap-east-1
  ```

- **或 SSO：`aws configure sso`**  
  若公司使用 IAM Identity Center，按控制台给的 SSO URL / 账户 / Role 配置 profile，然后同样 `export AWS_PROFILE=...`。

部署命令建议使用 **`--profile`** 或依赖 `AWS_PROFILE`，与上面一致：  
`npx aws-cdk@2 deploy --profile gwofy-hk --region ap-east-1 ...`

**2）Shopify 与 Gwofy 部署变量**

在项目根目录复制示例文件并编辑（文件名自定，下面用 `.env.local`）：

```bash
cp .env.example .env.local
# 用编辑器填写 SHOPIFY_*、GWOFY_API_CERTIFICATE_ARN 等（勿提交 .env.local）
```

`.env.local` 建议使用 **`export KEY=value`** 形式，每行一个变量。加载后再执行 CDK：

```bash
set -a
source .env.local
set +a
npx aws-cdk@2 synth
npx aws-cdk@2 deploy --region ap-east-1 "GwofyGuardStorage-dev" "GwofyGuardApi-dev"
```

说明：**Python/CDK 不会自动读取 `.env` 文件**；上述 `source` 只是把变量写入当前 shell，等价于手动 `export`。

**3）可选：每次进目录自动加载（direnv）**

若已安装 [direnv](https://direnv.net/)，可在项目根添加 `.envrc`，只放 **非敏感** 项（如 `AWS_PROFILE`、区域）；**勿把 Shopify Secret 写进会被提交的 `.envrc`**。敏感变量仍放在 **`.env.local`**，进目录后若需部署再执行一次：

```bash
set -a && source .env.local && set +a
```

或在 `.envrc` 末尾使用 direnv 自带的 dotenv 加载（此时 `.env.local` 需为 `KEY=value` 一行一个、无 `export` 前缀）：

```bash
export AWS_PROFILE=gwofy-hk
export AWS_REGION=ap-east-1
export AWS_DEFAULT_REGION=ap-east-1
dotenv_if_exists .env.local
```

执行 `direnv allow`。若你沿用仓库里带 `export` 的 `.env.local`，请继续用手动 `source .env.local`，勿混用 `dotenv_if_exists`。

### CI（以 GitHub Actions 为例）

**1）在仓库配置 Secrets**

GitHub → **Settings → Secrets and variables → Actions → New repository secret**，至少添加：

| Secret 名称 | 含义 |
|-------------|------|
| `AWS_ROLE_ARN` | 若用 OIDC 假设角色：IAM Role ARN（推荐） |
| 或使用长期密钥（不推荐久留）：`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` | 与 IAM 用户对应 |
| `SHOPIFY_CLIENT_ID` | Partner 应用 Client ID |
| `SHOPIFY_CLIENT_SECRET` | Partner 应用 Secret |
| `ADMIN_COGNITO_USER_POOL_ID` | 已有 Cognito User Pool ID（与 Api 栈 JWT 一致） |
| `ADMIN_COGNITO_CLIENT_ID` | **可选**。省略时部署会在该 Pool 内 **创建或复用** 名为 **`GWO-SHIPPING-PROTECTION`** 的 App Client，并将其 ID 作为 JWT `aud`（见栈输出 `AdminCognitoUserPoolClientId`）。若你方已建好 Client，可填此项以跳过 Custom Resource。 |
| `GWOFY_API_CERTIFICATE_ARN` | `ap-east-1` 的 ACM 证书 ARN |

区域可通过 workflow 里写死 `ap-east-1`，或再加 Secret `AWS_REGION`。

**2）Workflow 里注入环境并部署（OIDC 示例骨架）**

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write   # OIDC
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: ap-east-1
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: npx aws-cdk@2 deploy --require-approval never "GwofyGuardStorage-dev" "GwofyGuardApi-dev"
        env:
          SHOPIFY_CLIENT_ID: ${{ secrets.SHOPIFY_CLIENT_ID }}
          SHOPIFY_CLIENT_SECRET: ${{ secrets.SHOPIFY_CLIENT_SECRET }}
          ADMIN_COGNITO_USER_POOL_ID: ${{ secrets.ADMIN_COGNITO_USER_POOL_ID }}
          # 可选：省略则由 Custom Resource 确保 Pool 内存在 GWO-SHIPPING-PROTECTION App Client
          ADMIN_COGNITO_CLIENT_ID: ${{ secrets.ADMIN_COGNITO_CLIENT_ID }}
          GWOFY_API_CERTIFICATE_ARN: ${{ secrets.GWOFY_API_CERTIFICATE_ARN }}
```

首次在 AWS 账号侧需创建 **信任 GitHub OIDC 的 IAM Role**，并把 ARN 填入 `AWS_ROLE_ARN`。具体信任策略以 AWS 文档为准。

**GitLab CI**：在 **Settings → CI/CD → Variables** 中设置同名变量（可勾选 Masked）；在 `.gitlab-ci.yml` 的 `script` 里 `export` 或使用 `variables:` 块，同样在执行 `cdk deploy` 前注入。

### 安全清单

- 不要把 `.env.local`、含密钥的 `.envrc` 提交到仓库。  
- `SHOPIFY_CLIENT_SECRET`、证书 ARN、AWS 密钥仅出现在本机文件或 CI Secrets。  
- 轮换密钥时在 Partner Dashboard / IAM / ACM 侧更新，并同步改 Secrets / `.env.local`。

## 部署

1. 安装依赖：`pip install -r requirements.txt`（开发依赖：`pip install -r requirements-dev.txt`）。
2. 设置 Shopify 凭证供合成/部署使用（CDK 会将其写入 Lambda 环境变量），并配置 **已有 Cognito**（管理端 `/admin` JWT；本栈 **不再创建** User Pool）：

   ```bash
   export SHOPIFY_CLIENT_ID=...
   export SHOPIFY_CLIENT_SECRET=...
   export ADMIN_COGNITO_USER_POOL_ID=...   # 例 us-east-1_xxxx
   # 可选：export ADMIN_COGNITO_CLIENT_ID=...  # 已有 App Client；不设则自动确保名为 GWO-SHIPPING-PROTECTION 的 Client
   # 若 Pool 不在 Api 栈部署区域，再设：export ADMIN_COGNITO_REGION=ap-east-1
   ```

   可选：`WEBHOOK_BASE_URL`（与 API Gateway 根 URL 同源，例如 `https://xxxx.execute-api.region.amazonaws.com`）、`POST_INSTALL_REDIRECT_URL`、`FEISHU_WEBHOOK_URL`。

   也可使用 CDK context：`-c shopify_client_id=... -c shopify_client_secret=... -c webhook_base_url=...`，以及 **`-c admin_cognito_user_pool_id=...`**（可选 `-c admin_cognito_client_id=...`、`-c admin_cognito_region=...`）。

3. 合成 / 部署（默认 `stage=dev`，栈名为 `GwofyGuardStorage-dev` / `GwofyGuardApi-dev`）：

   ```bash
   npx aws-cdk@2 synth
   npx aws-cdk@2 deploy "GwofyGuardStorage-dev" "GwofyGuardApi-dev"
   ```

   **测试 / 预发 / 生产（同一 AWS 账号）**：通过 `stage` 区分多套独立栈与资源（建议 dev/staging/prod 各用一套密钥与 Partner 应用配置）：

   ```bash
   export SHOPIFY_CLIENT_ID=...   # 可与 dev 不同（若在 Partner 创建了单独的 Custom app）
   export SHOPIFY_CLIENT_SECRET=...
   export ADMIN_COGNITO_USER_POOL_ID=...
   # export ADMIN_COGNITO_CLIENT_ID=...  # 可选，见上文
   export WEBHOOK_BASE_URL=https://xxxx.execute-api....amazonaws.com   # 部署后填写 Api 栈对应的 URL

   # 开发联调
   npx aws-cdk@2 deploy -c stage=dev "GwofyGuardStorage-dev" "GwofyGuardApi-dev"

   # 生产（数据保留：表/KMS 使用 RETAIN，见 `cdk.json` 中的 context `retain_data`）
   npx aws-cdk@2 deploy -c stage=prod -c retain_data=true \
     "GwofyGuardStorage-prod" "GwofyGuardApi-prod"
   ```

   也可使用环境变量：`CDK_STAGE=prod`（与 `-c stage=` 二选一，以 context 为准）。

   **DynamoDB / SQS 物理名**：表名为 `gwofy-guard-{stage}`（例如 `gwofy-guard-dev`）；工作队列与 DLQ 使用同一前缀，便于在控制台区分环境。

   **部署到香港（`ap-east-1`）**：先指定区域再部署，例如：

   ```bash
   export AWS_REGION=ap-east-1
   export AWS_DEFAULT_REGION=ap-east-1
   # 或使用：npx aws-cdk@2 deploy --region ap-east-1 ...
   ```

   **证书区域必须与栈一致**：API Gateway 自定义域名使用的 ACM 证书 **只能** 与 HTTP API 在同一区域。若在 **us-west-2**（或其它区域）已有 `*.gwofy.com` 证书，**不能**把该 ARN 用于香港部署；请在 **ACM → 区域选「亚太地区（香港）ap-east-1」**，对 `*.gwofy.com` **再申请一张或导入**证书，部署时使用 **香港区域内** 的新 ARN，例如：

   ```bash
   export GWOFY_API_CERTIFICATE_ARN=arn:aws:acm:ap-east-1:你的账号:certificate/......
   ```

4. 复制栈输出中的 **HttpApiUrl**，在 Partner Dashboard / `shopify.app.toml` 中将重定向 URI 设为 `{HttpApiUrl}/oauth/callback`，Webhook URL 设为 `{HttpApiUrl}/webhooks/shopify`。

   **Webhook 订阅**：topic 列表以仓库根目录 `shopify.app.toml` 的 `[webhooks]` 为准（含 **`products/delete`、`orders/delete`、`markets/delete`** 等删除感知 topic；随应用配置同步到 Shopify）。OAuth 回调 **不再** 调用 Admin REST 注册 webhooks，避免与 App 配置 **重复订阅**、同一事件多次投递。

   **Shopify 侧**：同一应用可在 Partner Dashboard 配置 **多个 redirect URL**（dev/staging/prod 各一条）；Webhook 地址亦可按环境各配一条。也可为 dev/prod 分别创建 Custom app，隔离 `client_id`。

5. 安装流程：将商家引导至你应用的 Shopify OAuth 授权地址；回调命中 `/oauth/callback`。Worker 的 **`INITIAL_SYNC`** 仅做 **店铺资料**（币种、市场等）与 **自动激活**（创建 Shipping Protection 商品）；完成后 **异步入队 `CATALOG_SYNC`**（商品/订单）与 **`THEME_SYNC`**（Online Store 主题及文件）。激活失败不阻塞入队；错误写入 `last_activation_error`，可稍后 **`POST /api/activate`** 重试。

   **`read_themes` scope**：`shopify.app.toml` 已包含该 scope；Partner Dashboard 须一致。**已安装店铺** 若未重新授权，主题同步会跳过（ACCESS_DENIED），不影响安装与其它同步。管理端可 **`POST /admin/shops/{shop}/sync`**，`resources` 含 **`themes`** 手动补拉。

   **可过期离线 token（Shopify 2025-12+）**：向 `https://{shop}/admin/oauth/authorize` 发起 **offline** 授权时，查询串须包含 **`expiring=1`**（与 [Shopify 文档](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/offline-access-tokens) 一致）。本仓库 `/oauth/callback` 换票已固定带 **`expiring=1`**，并在 Dynamo **METADATA** 写入 `refresh_token_enc`、`shopify_offline_access_token_expires_at`、`shopify_offline_refresh_token_expires_at`。Worker / 商户 / 管理接口在调用 Admin API 前会 **自动 refresh**；若仍为历史 **非过期** token，会在首次请求时 **一次性迁移** 为可过期对（旧 token 随即作废）。仅用 `POST /admin/tools/decrypt-shopify-token` 取出的明文若未经过上述逻辑，在 Postman 里可能仍被 Shopify 拒绝，请触发任意已接线路径或重装授权。
### 自定义域名（gwofy.com）

约定：**子域名为 `sp-{stage}` + 根域名 `gwofy.com`**，与 CDK `stage` 一致。例如：

| `stage`（`-c stage=` / `CDK_STAGE`） | 对外主机名 |
|--------------------------------------|------------|
| `dev` | `sp-dev.gwofy.com` |
| `stage` | `sp-stage.gwofy.com` |
| `prod` | `sp-prod.gwofy.com` |

1. **ACM 证书**：在 **与 API Gateway 相同的 AWS 区域** 申请或导入证书（推荐使用 `*.gwofy.com` 通配符，一张证书覆盖所有环境子域）。
2. **部署时传入证书 ARN**（二选一）：

   ```bash
   export GWOFY_API_CERTIFICATE_ARN=arn:aws:acm:REGION:ACCOUNT:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   npx aws-cdk@2 deploy -c stage=dev "GwofyGuardStorage-dev" "GwofyGuardApi-dev"
   ```

   或使用 context：`-c certificate_arn=...`

3. **DNS（二选一）**  
   - **自动（Route 53 托管 `gwofy.com`）**：部署 Api 栈前同时设置 **`GWOFY_ROUTE53_HOSTED_ZONE_ID`**（Hosted zone ID）与 **`GWOFY_ROUTE53_ZONE_NAME`**（例如 `gwofy.com`，须与自定义域名后缀一致）。CDK 会在该公有区创建 **`sp-{stage}.gwofy.com` → API Gateway** 的别名 **A** 记录（IPv4）。等价 context：`-c route53_hosted_zone_id=... -c route53_zone_name=gwofy.com`。  
     - 若同名记录已在 Route 53 里手工创建且 **不在本 CloudFormation 栈管理**，再次部署可能冲突；请先删除手工记录或改用栈接管。  
     - CDK **不会**在未提供上述两项时访问 Route 53；未设置时仍需手工 DNS。  
   - **手工**：在栈 **Outputs** 查看 **CustomDomainRegionalTarget**，将 `sp-dev.gwofy.com` 等对 API Gateway 区域域名做 **CNAME**（或以别名记录指向输出目标）。

   API Gateway 控制台中的 API **名称**为 **`gwofy-guard-api-{stage}`**（例如 dev / prod），便于区分环境。

4. **Webhook / OAuth 根地址**：只要提供了证书 ARN，CDK 会将 Lambda 的 `WEBHOOK_BASE_URL` **默认设为 `https://sp-{stage}.gwofy.com`**（除非你显式设置了 `WEBHOOK_BASE_URL`）。Partner Dashboard 与 `shopify.app.toml` 应使用栈输出 **PublicApiUrl**（或同一 HTTPS 根地址），路径仍为 `/oauth/callback`、`/webhooks/shopify`。
5. **可选覆盖**：完整主机名可用环境变量 `GWOFY_CUSTOM_DOMAIN` 或 `-c custom_domain_name=`（一般无需修改）。
6. **命名约定**：根域名与前缀可通过 `GWOFY_DOMAIN_BASE` / `GWOFY_SUBDOMAIN_PREFIX` 或 context `gwofy_domain_base`、`gwofy_subdomain_prefix` 调整（默认 `gwofy.com` + `sp`）。
7. **部署前校验**：若手工设置了 `WEBHOOK_BASE_URL` 且与 `https://sp-{stage}.gwofy.com` 不一致，合成时会 **告警**；加上 `-c strict_deploy_config=true` 时 **合成失败**，避免配错环境。

**部署后检查**（解析 DNS + 探测 HTTPS）：

```bash
python3 scripts/check_gwofy_deploy.py --stage dev
# 或指定主机：python3 scripts/check_gwofy_deploy.py --host sp-dev.gwofy.com
```

## 商户 API、激活与管理员（Cognito）

部署 **Api** 栈后，除原有 OAuth / Webhook 外，还提供：

### 商户端（Shopify Session Token JWT）

路径均位于 `{HttpApiUrl}` 根下，请求头 `Authorization: Bearer <session_token>`（与 [Session token](https://shopify.dev/docs/apps/auth/session-tokens) 一致）。

**离线 token 自动补救**（所有上表 Session 路由，不含 `/api/cart-config`）：若 `installation_status=OFFLINE_AUTH_EXPIRED`、缺少 `refresh_token_enc`、或 refresh 将在 **7** 天内过期（`OFFLINE_REFRESH_RECOVERY_WINDOW_DAYS`），后端用当前 Session Token 做 [token exchange](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/token-exchange) 换新 offline 对并恢复 **ACTIVE**（审计 `OFFLINE_TOKEN_SESSION_RECOVERY`）；关键失败 **401** `shopify_offline_auth_failed` / **502** `offline_token_recovery_failed`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/me` | 返回 `auth_id`（即 `store_number`）、`activation_status`、险种状态、`shop_currency_code`、`embed_deep_link`、`last_activation_error`（激活失败时的 JSON 字符串，成功激活后清除）等。`embed_deep_link` 为 `https://admin.shopify.com/store/{shop_handle}/themes/{theme_id}/editor?context=apps&appEmbed=...&previewPath=...`（`theme_id` 取自 **MAIN** 主题；无缓存时 `/api/me` 会 live 拉取并写入 `main_theme_gid`，仍未知则为空字符串） |
| POST | `/api/activate` | **同步**激活或再次激活（安装后 Worker 也会自动尝试一次）：商户 Lambda 内用店铺离线 token 创建/更新 Shipping Protection 商品并写回 `protection_product_gid`。成功 **200** `{"ok":true,"activation_status":"ACTIVATED"}`；业务错误 **400**（如 `shop_profile_not_ready`、`currency_not_supported`、`pricing_not_configured`）；解密/Shopify 异常 **500/502**。激活**不会**调用 `sync_shop_profile`；`shop_currency_code` 须已由安装全量同步 / webhook / 定时对账写入 METADATA。HTTP API 与 Lambda 超时请见栈配置（商户 Lambda 已放宽以便多变体 upsert）。 |
| PATCH | `/api/me/embed` | JSON body：`{"embed_enabled_ack": true}` |
| POST | `/api/cart-config` | **无 Session JWT**（同上 HMAC）。**必填** `country`（ISO2）、`shopDomain`。须 **`activation_status=ACTIVATED`**，否则 **403** `shop_not_activated`。国家不在全局支持列表 → **400** `country_not_supported`（`country` 仅用于 **费率** 等，**不参与保额**）。`X-Gwofy-Shop` 须与 `shopDomain` 主机名一致。**响应** `calcInfo` 含 **`maxAmount`**、**`maxAmountCurrency`**、`spRate` 等；并含 **`merchantPremiumRules`**（从店铺 `METADATA.merchant_premium_rules_json` 解析，与 **`GET /api/me/merchant-premium-rules`** 同源；无效或缺失时为默认空规则）。**服务端不下发最终加价后金额**， storefront / App 按规则自行计算。 |
| GET | `/api/me/merchant-premium-rules` | 返回 **`{"merchantPremiumRules":{...}}`**（店主配置的加成 / 满减等；店铺须 **ACTIVE** 且未挂起）。 |
| PUT | `/api/me/merchant-premium-rules` | JSON body 为完整规则对象（见 `lambda/lib/merchant_premium_rules.py` 校验）；写入 **`merchant_premium_rules_json`**。**400** `invalid_merchant_premium_rules` + `detail`。 |
| POST | `/api/shop-enabled-currencies/sync` | **Session JWT**（与 `/api/me` 相同）。从 Shopify 拉取 **店铺已启用货币** 写入 `shop_enabled_currencies_json`；**200** `{"ok":true,"currencies":[...],"synced_at":"..."}`。配置 `sp_max_coverage_by_currency` 前若尚未同步会 **400** `shop_enabled_currencies_not_synced`。 |

**店主规则语义摘要**（`merchantPremiumRules`）：`markup.default` 与 `markup.byCountry`（ISO2）为在平台档位价上的 **`addPercent`（%）** 与店铺结算货币下的 **`addFixed`**；`promotions` 为满减阶梯。字段 **`promotionApplyMode`** 当前仅支持 **`highest_threshold_wins`**：客户端在购物车小计满足条件的规则中，仅采用 **`minCartSubtotal` 最大** 的一条折扣（不累加）。建议计算顺序：**平台档位价 → markup → 满减**；与 Shopify 结账变体标价是否一致由 App 侧处理。

可选环境变量（CDK 写入 Worker / 商户 Lambda，也可用 `-c` context）：`ORDER_PROTECTION_TAG`（默认 `gwofy-shipping-protection`）— 仅用于在 **本系统 DynamoDB 订单镜像** 上写入 `sync_tags`（**不会**调用 Shopify 修改商户订单）。

激活时在商户店创建/更新 **UNLISTED** 运费险商品，**handle** 固定为 **`GWOFY-SHIPPING-PROTECTION-QAQWER`**，变体 **Plan = S0001…S0098**，每变体 **SKU = `plan_code`**。变体**标价**为管理员在该店铺**结算货币**下配置的 **原生金额**（字段 **`price`**，兼容读取旧数据中的 **`price_usd`**）；**激活不再使用汇率**。档位与购物车 subtotal 的映射仍按 **数组顺序** 将「有效最大保额」在 **USD 分量**（`effective_max_coverage_usd` / 合并 map 的 `USD`）上均分为 `len(tiers)` 段（由客户端在本地用购物车金额完成档位换算）。激活成功后会 **REMOVE** 历史上可能存在的 `last_fx_*` 字段。**禁止删除** Dynamo 里曾出现过的 `plan_code`（仅可加档或改价）。仍含 **`min_usd`** 的旧数据走兼容解析。首装缺省由 Worker 种子 **`PRICING_MODEL#USD`** + **`SUPPORTED_CURRENCIES`**（默认仅 `USD`）、**`MAX_COVERAGE_BY_CURRENCY`**（默认 `{"USD":9000}`）、`SHIPPING_COUNTRY_DEFAULTS`（各国仅 `rate`）；若仍存在旧版 **`PRICING_MODEL_DEFAULT`** 行则迁移到 USD 定价行。

**全局支持国家**（`GLOBAL#CONFIG` / `SHIPPING_COUNTRY_DEFAULTS`）：`GET/PUT /admin/config/shipping-countries`，body 示例：`{"countries":{"US":{"rate":"0.04"},"CA":{"rate":"0.05"}}}`（**每国仅 `rate`**；不再按国存保额）。未出现在该对象中的国家 **不支持**。Worker 首次同步会 **种子** 一批常见国家；可整体替换。`shop/update` 与 **markets** webhook 仅对 **支持列表内的国家** 在 `sp_market_rates_json` 中自动写入 **全局配置里该国的默认 `rate`**。

**全局保额（按币种，不按国）**：`GLOBAL#CONFIG` / **`MAX_COVERAGE_BY_CURRENCY`**。`GET/PUT /admin/config/max-coverage-by-currency`，body：`{"amounts":{"USD":9000,"EUR":8200}}`（键须为管理员允许列表中的 ISO 4217）。

**店铺覆盖**：`PUT /admin/shops/{shop}/shipping-calc-settings`（**Gwofy 管理员**配置）可更新 `sp_market_rates`（按国 **费率**）、`sp_max_coverage_usd`（legacy 全店 USD 兜底）、**`sp_max_coverage_by_currency`**（按 **币种** 覆盖保额，与全局 merge；键须为 **店铺已启用货币 ∩ 平台允许**）。店主自定义的保费加减 / 满减规则 **不在此接口**，由店主在 App 内调用 **`PUT /api/me/merchant-premium-rules`**。**`sp_country_max_overrides` 已废弃**（**400** `deprecated_sp_country_max_overrides`）。写入 `sp_max_coverage_by_currency` 前须先 **`POST /api/shop-enabled-currencies/sync`** 或 **`POST /admin/shops/{shop}/sync-enabled-currencies`**（否则 **400** `shop_enabled_currencies_not_synced`）。**有效费率** = 店铺该国 `sp_market_rates`（若有）否则全局该国 `rate`。**有效保额** = 全局 `amounts` 与店铺 `sp_max_coverage_by_currency_json` 按币种 merge 后，取 **`shop_currency_code`** 对应金额（无则 `USD`，再无则 `sp_max_coverage_usd`，最后 9000）。OAuth / `INITIAL_SYNC` / `sync_shop_profile` 会拉取 **店铺启用货币** 列表。Partner 需 **`read_markets`**；读取 `currencySettings` 建议含 **`read_shop`**（见 `shopify.app.toml`）。

### 管理员（Cognito JWT + 用户组）

- **User Pool / App Client**：使用你们 **已存在的** Cognito User Pool。部署前必须设置 **`ADMIN_COGNITO_USER_POOL_ID`**（或 context `admin_cognito_user_pool_id`）。**`ADMIN_COGNITO_CLIENT_ID`**（或 `admin_cognito_client_id`）为 **可选**：若省略，Api 栈会通过 **Custom Resource** 在 Pool 内 **列出** 已有 clients，若无名为 **`GWO-SHIPPING-PROTECTION`** 的 App Client 则 **创建**（`GenerateSecret=false`，常见浏览器登录流），并把得到的 **ClientId** 写入 JWT Authorizer 的 `aud` 与输出 `AdminCognitoUserPoolClientId`。若 Pool 不在 Api 栈所在 AWS 区域，再设 **`ADMIN_COGNITO_REGION`**（或 `admin_cognito_region`）。**仅缺少 User Pool ID 时** `cdk synth` / `deploy` 会报错。删除 CloudFormation 栈时 **不会** 删除该自动创建的 App Client（Delete 请求中不调用 Cognito 删除）。
- 栈 **Outputs**：`AdminCognitoUserPoolId`、`AdminCognitoUserPoolClientId`、`AdminCognitoIssuer`、`AdminCognitoRegion`。用户与密码由 **你们既有身份平台** 管理。
- 调用 `/admin/...` 时请求头使用 **`Authorization: Bearer <Cognito Id Token>`**（须含 `cognito:groups` 声明）。除 API Gateway 对 JWT 的签名校验外，Lambda 会要求调用者属于你们 **已存在的** 用户组 **`GWOFY-SHIPPING-PROTECTION`**（本栈 **不会** 创建该组；请在你们身份平台侧维护成员）。未入组返回 **403** `forbidden_not_in_admin_group`。
- 可通过环境变量或 CDK context **`admin_cognito_group`** / `ADMIN_COGNITO_GROUP` 覆盖默认组名（一般保持 `GWOFY-SHIPPING-PROTECTION` 即可）。
- **Cognito Hosted UI 回调**：`GET /auth/callback`（**无需** JWT）。浏览器从 Cognito **`/oauth2/authorize`** 授权后带 `?code=` 重定向至此；Lambda 向 Cognito **`/oauth2/token`** 换 token，默认返回 **HTML**（可复制 **Id token** 用于 `Authorization: Bearer`）；请求头 **`Accept: application/json`** 时返回 JSON。**回调 URL** 由 **`WEBHOOK_BASE_URL` + `/auth/callback`** 组成（须与 Cognito App Client 里配置的 Allowed callback URLs **完全一致**）。部署前还需设置 **`COGNITO_HOSTED_UI_DOMAIN`**（或 `-c cognito_hosted_ui_domain=`），值为 Cognito **域名前缀主机名**，例如 **`ap-east-1xxxx.auth.ap-east-1.amazoncognito.com`**（不要带 `https://`）。
- 路由前缀 `/admin`（API Gateway JWT 校验 issuer + audience = `AdminCognitoUserPoolClientId`）：
  - `GET /admin/shops`（query：`status=ACTIVE`、`limit`、`cursor`）；列表项含 **`main_theme_gid`**（MAIN 主题 GID，主题同步或 `/api/me` live 拉取后写入）
  - `GET /admin/shops/{shop}`（`shop` 需 URL 编码；`shop` 对象内含 **`shop_enabled_currencies`** 解析数组，便于配置保额 UI）
  - `GET /admin/shops/{shop}/detail` — 与上一行相同返回完整 **`shop`** METADATA（**不脱敏**，含 `access_token_enc` 等，仅限可信管理环境）；并返回 **`merchantPremiumRules`**（解析自 `merchant_premium_rules_json`）。若存储 JSON 无效则 **`merchantPremiumRules`** 为默认空规则且可能带 **`merchant_premium_rules_parse_warning`**。
  - `POST /admin/tools/decrypt-shopify-token`，body：`{"access_token_enc":"<KMS Base64 密文>","kms_key_id":"<可选，缺省用 Lambda KMS_KEY_ID>","shop":"<可选，审计归属店铺 host；缺省为内部占位>"}` → **200** `{"ok":true,"access_token":"<明文 Shopify token>"}`（**极高敏感**，仅管理组；失败 **502** `decrypt_failed`；写审计 **`ADMIN_DECRYPT_SHOPIFY_TOKEN`**，**detail 不含明文**）
  - `POST /admin/shops/{shop}/features/return-insurance`、`.../shipping-protection`，body：`{"status":"CLOSED"|"OPEN_UNAUDITED"|"OPEN_AUDITED"}`
  - `POST /admin/shops/{shop}/suspend`、`.../resume`
  - `GET /admin/shops/{shop}/products`、`GET /admin/shops/{shop}/orders`：商品/订单行含 **`payload`（JSON 快照）** 与顶栏筛选字段。商品含 **`product_handle`、`product_title`、`product_status`（Shopify 状态）、`price_min`/`price_max`、`variant_count`、`sync_deleted`、`deleted_at`**；订单含 **`order_name`、`legacy_resource_id`、`display_financial_status`、`display_fulfillment_status`、`current_total_price`（Decimal）、`sync_deleted`、`deleted_at`**。查询参数：`include_deleted=true` 含已删除镜像；商品可加 `product_handle_prefix`、`product_status`；订单可加 `financial_status`（与 `display_financial_status` 匹配）、`order_name_prefix`；原：`only_protection`、`tag`；`limit` 默认 100、最大 500。Webhook **`products/delete` / `orders/delete`** 会将对应 `PRODUCT#` / `ORDER#` 标为 `sync_deleted`（保留最后 `payload`）；**`markets/delete`** 触发重新拉 Markets 并 **修剪** `sp_market_rates_json` 中已不在市场的国家键。
  - `GET /admin/shops/{shop}/audit`（审计流水）
  - `GET /admin/config/supported-currencies` → `{"currencies":["USD","EUR",...]}`（管理员启用的币种，须为代码内允许列表的子集）
  - `PUT /admin/config/supported-currencies`，body：`{"currencies":["USD","EUR"]}`（非空、去重、大写存储）
  - `GET /admin/config/pricing-model/{currency}` → `{"currency":"USD","tiers":[...]}`（与 `PUT` 体中 `tiers` 同形；**USD** 无表内数据时 `tiers` 为代码内默认 98 档）
  - `PUT /admin/config/pricing-model/{currency}`，body：`{"tiers":[...]}`（1–200 条；每条 **`plan_code`** + **`price`**（原生标价）；兼容 **`price_usd`** 作为数值来源；可选 **`sku`** 仅作 `plan_code` 别名；**`plan_code` 唯一**；不可删掉历史上已有的档位编码；`{currency}` 须为 **允许列表** 中的 ISO 4217）
  - `GET /admin/config/pricing-model` → 与 **`GET .../pricing-model/USD`** 等价（兼容旧客户端）
  - `PUT /admin/config/pricing-model` → **400** `deprecated_use_pricing_model_currency`，请改用带币种路径的 `PUT`
  - `GET /admin/config/shipping-countries`、`PUT /admin/config/shipping-countries`，body：`{"countries":{...}}`（每国仅 **`rate`**；允许空对象表示暂不支持任何国家）
  - `GET /admin/config/max-coverage-by-currency` → `{"amounts":{"USD":9000,...}}`；`PUT /admin/config/max-coverage-by-currency`，body：`{"amounts":{...}}`（全局按 **币种** 的保额默认）
  - `GET /admin/config/activity-info`、`PUT /admin/config/activity-info`，body：`{"activityExtInfo":"<字符串>","activityState":<整数>}` — 与 **`POST /api/cart-config`** 返回里 `dataInfo.activityInfo` 同源（购物车活动占位；商户侧仅一条运费险 Shopify 商品，**运费险 / 退货险开关**见各店 `shipping_protection_status` / `return_insurance_status`，不由多件商品区分）
  - `GET /admin/config/tips-info`、`PUT /admin/config/tips-info`，body：`{"ppVersion":{"faqUrl":"","locationType":"","popup":"","terms":""},"spVersion":{"faqUrl":"","popup":"","terms":""}}` — 与 **`POST /api/cart-config`** 返回里 `tipsInfo` 同源（PP / SP 文案区 FAQ、条款链接、`popup` 等）
  - `GET /admin/config/calc-coverage-tips`、`PUT /admin/config/calc-coverage-tips`，body：`{"spBelowMinCoverageTip":"<字符串>","spGreaterMaxCoverageTip":"<字符串>"}` — **全局**购物车 **`calcInfo`** 保额提示文案（默认空串）
  - `GET /admin/shops/{shop}/calc-coverage-tips` → `global`、`shopOverride`（店铺是否覆盖）、`effective`（店铺请求 **`/api/cart-config`** 时实际下发）
  - `PUT /admin/shops/{shop}/calc-coverage-tips`，body 可只含其一或两项：`spBelowMinCoverageTip`、`spGreaterMaxCoverageTip`；值为 **`null`** 表示删除该字段覆盖并回退全局 — 与 **`POST /api/cart-config`** 里 **`calcInfo.spBelowMinCoverageTip` / `spGreaterMaxCoverageTip`** 同源（**已移除** `calcInfo` 中的 `spMaxCoverage`、`spMinCoverage`、`zeroBuyConf`，保额上限仍以 **`maxAmount`** 表示）
  - `PUT /admin/shops/{shop}/shipping-calc-settings`，body 可含其一或多项：`sp_max_coverage_usd`、`sp_market_rates`、**`sp_max_coverage_by_currency`**（不再接受 `sp_country_max_overrides`）
  - `POST /admin/shops/{shop}/sync-enabled-currencies` — 用店铺离线 token 拉取 Shopify 启用货币列表（与商户 **`POST /api/shop-enabled-currencies/sync`** 同源逻辑）
  - `POST /admin/shops/{shop}/sync` — **手动拉取/更新** 店铺镜像数据。Body：`{"resources":["all"]}` 或 `["shop_profile","products","orders","currencies","markets","catalog"]`（`catalog` = 商品+订单）；**`async`** 默认 **`true`**（入队 Worker，**202**）；**`false`** 时在 Admin Lambda 内同步执行（**29s** 超时，仅适合 profile/currencies/markets）。**`reset_checkpoints`**：`true` 时商品/订单全量从第一页重拉。同步模式成功 **200** / 部分失败 **502**，返回 **`steps`** 各资源结果。

定价 / 变体模板 / 支持国家（仅 rate）/ **按币种全局保额** `MAX_COVERAGE_BY_CURRENCY` / **activityInfo** / **tipsInfo** / **calc-coverage-tips（全局）** 缺省时 Worker 会种子写入 `GLOBAL#CONFIG` 下 **`PRICING_MODEL#USD`**（默认 98 档）、**`SUPPORTED_CURRENCIES`**（默认 `["USD"]`）、**`MAX_COVERAGE_BY_CURRENCY`**（默认 `USD:9000`）、`SHIPPING_COUNTRY_DEFAULTS`（各国仅 `rate`）、**`ACTIVITY_INFO`**、**`TIPS_INFO`** 与 **`CALC_COVERAGE_TIPS`**（默认可为空文案）。旧版 **`PRICING_MODEL_DEFAULT`** 若存在则迁移到 **`PRICING_MODEL#USD`**。**店铺级**提示写在对应店铺 **`METADATA`** 的 `sp_below_min_coverage_tip` / `sp_greater_max_coverage_tip`。DynamoDB 表含 **GSI2**（`SHOP_INDEX`）用于列举店铺。

**迁移说明**：若 Dynamo 中 `SHIPPING_COUNTRY_DEFAULTS` 仍含各国 **`max_coverage_usd`**，新代码**不再读取**该字段作为保额；请用 **`PUT /admin/config/max-coverage-by-currency`** 与店铺 **`sp_max_coverage_by_currency`** 表达保额。历史 **`sp_country_max_overrides`** 仅保留在 METADATA 中不再生效；请改用按币种 map 并调用启用货币同步后再写店铺覆盖。

---

## CDK Python 项目说明

本项目用于 CDK（Python）开发。`cdk.json` 指定 CDK Toolkit 如何执行应用。

目录结构接近常规 Python 项目。若在初始化时创建了虚拟环境，通常位于 `.venv`。创建虚拟环境需要系统路径中有 `python3`（Windows 上可为 `python`）且可使用 `venv` 模块。若自动创建失败，可手动创建。

在 macOS / Linux 上手动创建虚拟环境：

```
python3 -m venv .venv
```

创建完成后激活：

```
source .venv/bin/activate
```

在 Windows 上激活：

```
.venv\Scripts\activate.bat
```

激活后安装依赖：

```
pip install -r requirements.txt
```

然后可合成 CloudFormation 模板（本项目推荐使用 `npx aws-cdk@2`）：

```
npx aws-cdk@2 synth
```

如需增加其他依赖（例如其他 CDK 库），将其写入 `requirements.txt` 并重新执行 `pip install -r requirements.txt`。

## 常用命令

- `npx aws-cdk@2 ls` — 列出应用中的全部栈  
- `npx aws-cdk@2 synth` — 输出合成后的 CloudFormation 模板  
- `npx aws-cdk@2 deploy` — 将栈部署到默认 AWS 账号/区域  
- `npx aws-cdk@2 diff` — 对比已部署栈与当前定义  
- `npx aws-cdk@2 docs` — 打开 CDK 文档  



## 部署
、、、
set -a && source .env.prod && set +a
npx aws-cdk@2 deploy -c stage=prod  "GwofyGuardStorage-prod" "GwofyGuardApi-prod" --region ap-east-1
、、、