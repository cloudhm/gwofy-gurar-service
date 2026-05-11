
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

   **Shopify 侧**：同一应用可在 Partner Dashboard 配置 **多个 redirect URL**（dev/staging/prod 各一条）；Webhook 地址亦可按环境各配一条。也可为 dev/prod 分别创建 Custom app，隔离 `client_id`。

5. 安装流程：将商家引导至你应用的 Shopify OAuth 授权地址；回调命中 `/oauth/callback`。

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

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/me` | 返回 `auth_id`（即 `store_number`）、`activation_status`、险种状态、`shop_currency_code`、`embed_deep_link` 等 |
| POST | `/api/activate` | 入队异步激活（Worker 创建/更新 Shipping Protection 商品、写回 `protection_product_gid`） |
| PATCH | `/api/me/embed` | JSON body：`{"embed_enabled_ack": true}` |
| POST | `/api/protection/resolve-variant` | **无 Session JWT**。同上 HMAC。Body 须含 `cart_subtotal`、`currency`（须与店铺结算货币一致）。**须已完成激活**（`activation_status=ACTIVATED`）。可选 `country`：若传入则须为全局支持国家，且会用该国 **有效最大保额（USD）** 校验购物车折算 USD 是否超限；不传 `country` 时仅用店铺级 `sp_max_coverage_usd`（或默认 9000）做上限校验。 |
| POST | `/api/cart-config` | **无 Session JWT**（同上 HMAC）。**必填** `country`（ISO2）。须 **`activation_status=ACTIVATED`**，否则 **403** `shop_not_activated`。国家不在全局支持列表 → **400** `country_not_supported`。可选 `cart_subtotal` + `currency`（须与店铺货币一致）：超过该国 **有效最大保额（USD）** → **400** `cart_exceeds_max_coverage`。`X-Gwofy-Shop` 须与 `shopDomain` 主机名一致。 |

可选环境变量（CDK 写入 Worker / 商户 Lambda，也可用 `-c` context）：`ORDER_PROTECTION_TAG`（默认 `gwofy-shipping-protection`）— 仅用于在 **本系统 DynamoDB 订单镜像** 上写入 `sync_tags`（**不会**调用 Shopify 修改商户订单）。

激活时 Worker 会在商户店创建 **UNLISTED** 运费险商品，**handle** 固定为 **`GWOFY-SHIPPING-PROTECTION-QAQWER`**，变体 **Plan = S0001…S0098**，每变体 **独立 SKU**（默认与 `plan_code` 相同）。档位与 **USD 加价**、**购物车保额区间**（默认按 0–9000 USD 均分 98 档）由全局 **`PUT /admin/config/pricing-model`** 的 `tiers` 定义（字段 `plan_code`、`min_usd`、`max_usd`、`price_usd`，可选 `sku` 覆盖库存 SKU）。首装缺省会种子 **98 档** 与你们提供的默认价表一致。

**全局支持国家**（`GLOBAL#CONFIG` / `SHIPPING_COUNTRY_DEFAULTS`）：`GET/PUT /admin/config/shipping-countries`，body 示例：`{"countries":{"US":{"rate":"0.04","max_coverage_usd":9000},"CA":{"rate":"0.05","max_coverage_usd":8000}}}`。未出现在该对象中的国家 **不支持**，无需配置费率/保额。Worker 首次同步会 **种子** 一批常见国家；可整体替换。`shop/update` 与 **markets** webhook 仅对 **支持列表内的国家** 在 `sp_market_rates_json` 中自动写入 **全局配置里该国的默认 `rate`**（不在支持列表的国家不会写入店铺费率）。

**店铺覆盖**：`PUT /admin/shops/{shop}/shipping-calc-settings` 可更新 `sp_market_rates`（某国 **特殊费率**，覆盖全局默认）、`sp_max_coverage_usd`（全店兜底保额）、`sp_country_max_overrides`（按国覆盖最大保额，如 `{"US":12000}`）。**有效费率** = 店铺该国费率（若有）否则全局该国 `rate`；**有效最大保额（USD）** = 店铺 `sp_country_max_overrides` 该国值 → 否则全局该国 `max_coverage_usd` → 否则 `sp_max_coverage_usd` → 否则 9000。Partner 需 **`read_markets`**（见 `shopify.app.toml`）。

### 管理员（Cognito JWT + 用户组）

- **User Pool / App Client**：使用你们 **已存在的** Cognito User Pool。部署前必须设置 **`ADMIN_COGNITO_USER_POOL_ID`**（或 context `admin_cognito_user_pool_id`）。**`ADMIN_COGNITO_CLIENT_ID`**（或 `admin_cognito_client_id`）为 **可选**：若省略，Api 栈会通过 **Custom Resource** 在 Pool 内 **列出** 已有 clients，若无名为 **`GWO-SHIPPING-PROTECTION`** 的 App Client 则 **创建**（`GenerateSecret=false`，常见浏览器登录流），并把得到的 **ClientId** 写入 JWT Authorizer 的 `aud` 与输出 `AdminCognitoUserPoolClientId`。若 Pool 不在 Api 栈所在 AWS 区域，再设 **`ADMIN_COGNITO_REGION`**（或 `admin_cognito_region`）。**仅缺少 User Pool ID 时** `cdk synth` / `deploy` 会报错。删除 CloudFormation 栈时 **不会** 删除该自动创建的 App Client（Delete 请求中不调用 Cognito 删除）。
- 栈 **Outputs**：`AdminCognitoUserPoolId`、`AdminCognitoUserPoolClientId`、`AdminCognitoIssuer`、`AdminCognitoRegion`。用户与密码由 **你们既有身份平台** 管理。
- 调用 `/admin/...` 时请求头使用 **`Authorization: Bearer <Cognito Id Token>`**（须含 `cognito:groups` 声明）。除 API Gateway 对 JWT 的签名校验外，Lambda 会要求调用者属于你们 **已存在的** 用户组 **`GWOFY-SHIPPING-PROTECTION`**（本栈 **不会** 创建该组；请在你们身份平台侧维护成员）。未入组返回 **403** `forbidden_not_in_admin_group`。
- 可通过环境变量或 CDK context **`admin_cognito_group`** / `ADMIN_COGNITO_GROUP` 覆盖默认组名（一般保持 `GWOFY-SHIPPING-PROTECTION` 即可）。
- **Cognito Hosted UI 回调**：`GET /auth/callback`（**无需** JWT）。浏览器从 Cognito **`/oauth2/authorize`** 授权后带 `?code=` 重定向至此；Lambda 向 Cognito **`/oauth2/token`** 换 token，默认返回 **HTML**（可复制 **Id token** 用于 `Authorization: Bearer`）；请求头 **`Accept: application/json`** 时返回 JSON。**回调 URL** 由 **`WEBHOOK_BASE_URL` + `/auth/callback`** 组成（须与 Cognito App Client 里配置的 Allowed callback URLs **完全一致**）。部署前还需设置 **`COGNITO_HOSTED_UI_DOMAIN`**（或 `-c cognito_hosted_ui_domain=`），值为 Cognito **域名前缀主机名**，例如 **`ap-east-1xxxx.auth.ap-east-1.amazoncognito.com`**（不要带 `https://`）。
- 路由前缀 `/admin`（API Gateway JWT 校验 issuer + audience = `AdminCognitoUserPoolClientId`）：
  - `GET /admin/shops`（query：`status=ACTIVE`、`limit`、`cursor`）
  - `GET /admin/shops/{shop}`（`shop` 需 URL 编码）
  - `POST /admin/shops/{shop}/features/return-insurance`、`.../shipping-protection`，body：`{"status":"CLOSED"|"OPEN_UNAUDITED"|"OPEN_AUDITED"}`
  - `POST /admin/shops/{shop}/suspend`、`.../resume`
  - `GET /admin/shops/{shop}/products`、`GET /admin/shops/{shop}/orders`（`only_protection=true`：`has_shipping_protection`；`tag=<字符串>`：仅返回 `sync_tags` 中含该值的订单；二者可组合）
  - `GET /admin/shops/{shop}/audit`（审计流水）
  - `PUT /admin/config/pricing-model`，body：`{"tiers":[...]}`（1–200 条；每条须含 `plan_code`、`min_usd`、`max_usd`、`price_usd`；可选 `sku` 作为 Shopify 库存 SKU）
  - `GET /admin/config/shipping-countries`、`PUT /admin/config/shipping-countries`，body：`{"countries":{...}}`（每国 `rate` + `max_coverage_usd`；允许空对象表示暂不支持任何国家）
  - `PUT /admin/shops/{shop}/shipping-calc-settings`，body 可含其一或多项：`sp_max_coverage_usd`、`sp_market_rates`、`sp_country_max_overrides`

定价 / 变体模板 / 支持国家缺省时 Worker 会种子写入 `GLOBAL#CONFIG` 下 `PRICING_MODEL_DEFAULT`（默认 98 档）与 `SHIPPING_COUNTRY_DEFAULTS`。DynamoDB 表含 **GSI2**（`SHOP_INDEX`）用于列举店铺。

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
