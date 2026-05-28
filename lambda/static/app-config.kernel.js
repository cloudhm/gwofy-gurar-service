/**
 * Gwofy Guard — 配置层（app-config.js）
 *
 * - 算价 / 限额 / SKU / 货币：__gwofy_calculate__*（稳定内核，随 extension 发版）
 * - 店面参数：内置 GWOFY_CONFIG；可选 remoteScriptUrls 按序加载远程补丁脚本后再 sync
 * - 配置就绪后加载 app-storefront.js（默认 GWOFY_CONFIG.remoteScriptUrls 中的 storefront URL）
 *
 * Liquid 可选注入：GWOFY_STOREFRONT_ASSET_URL（覆盖 storefront URL）、GWOFY_INITIAL_CART、GWOFY_LOCALIZATION
 */
(function (g) {
  "use strict";

  // ---------------------------------------------------------------------------
  // 店面样式（由 storefront injectStyles / injectTipsDialogStyles 注入）
  // ---------------------------------------------------------------------------

  /** 默认 widget / tipsDialog / extra 样式表 */
  function buildDefaultGwofyStyles() {
    return {
      widget: `
#gwofyWrapper {
  position: relative;
  margin: 10px 0;
}

#gwofyWrapper .gwofy_cnt_wrapper {
  position: relative;
  min-height: 62px;
  padding: 12px 0;
  background: #f8f8fa;
  border-radius: 6px;
  border: 1px solid #b9bdc8;
  box-sizing: border-box;
  max-width: 36rem;
  margin: 0 auto;
}

#gwofyWrapper .gwofy_cnt {
  position: relative;
  z-index: 1;
  flex: 1;
  padding: 0 clamp(2px, 2.5vw, 10px);
}

#gwofyWrapper .gwofy_bd {
  position: relative;
  display: flex;
  justify-content: space-between;
  z-index: 1;
  flex: 1;
  text-align: left;
}

#gwofyWrapper .gwofy_bd_title {
  font-size: 0;
  line-height: 1.3;
}

#gwofyWrapper .gwofy_bd_title_txt {
  display: inline;
  color: #1d1d1f;
  font-size: 14px;
  font-weight: 700;
  vertical-align: middle;
  cursor: pointer;
  word-wrap: break-word;
}

#gwofyWrapper .gwofy_bd_title_img {
  width: 16px;
  height: auto;
  vertical-align: middle;
  position: relative;
  top: -1px;
}

#gwofyWrapper .gwofy_bd_title_txt p,
#gwofyWrapper .gwofy_bd_title_txt p span {
  vertical-align: middle;
  margin: 0;
  padding: 0;
  display: inline;
  color: #1d1d1f;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}

#gwofyWrapper .gwofy_tips {
  display: inline;
  width: 12px;
  vertical-align: middle;
  margin-top: 0;
  margin-left: 0;
  cursor: pointer;
  line-height: 1.3;
}

#gwofyWrapper .gwofy_bd_desc {
  display: block;
  line-height: 1.3;
  margin-top: 5px;
  color: #6e6e73;
  font-size: 12px;
  word-wrap: break-word;
  text-align: left;
}

#gwofyWrapper .gwofy_bd_desc p,
#gwofyWrapper .gwofy_bd_desc p span {
  vertical-align: middle;
  margin: 0;
  padding: 0;
  display: inline;
  line-height: 1.3;
  color: #1d1d1f;
}

#gwofyWrapper .gwofy_ft {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

#gwofyWrapper .gwofy_price {
  min-height: 20px;
  color: #000;
  font-size: 12px;
  font-weight: 700;
  margin-top: 1px;
  white-space: nowrap;
  line-height: 1.3;
}

#gwofyWrapper.gwofy_theme_black .gwofy_cnt_wrapper {
  background: #18181c;
  border: 1px solid #43434f;
}

#gwofyWrapper.gwofy_theme_black .gwofy_bd_title_txt,
#gwofyWrapper.gwofy_theme_black .gwofy_bd_title_txt * {
  color: #dedede;
}

#gwofyWrapper.gwofy_theme_black .gwofy_bd_desc,
#gwofyWrapper.gwofy_theme_black .gwofy_bd_desc * {
  color: #999;
}

#gwofyWrapper.gwofy_theme_black .gwofy_price {
  color: #dedede;
}

#gwofyWrapper.gwofy_theme_black .gwofy_tips {
  filter: brightness(2);
}

#gwofyWrapper .gwofy_visually_hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.gwofy-checkout-without {
  display: block;
  width: 100%;
  text-align: center;
  margin: 10px 0;
  font-size: 14px;
  color: #6e6e73;
  text-decoration: underline;
  cursor: pointer;
  background: none;
  border: none;
  padding: 0;
}

.gwofy-checkout-without:hover {
  color: #1d1d1f;
}
`,
      tipsDialog: `
/* SP-DL-5 — 对齐 xcottons 说明弹窗 */
#gwofySpTipsDialog {
  border: none;
  padding: 0;
  margin: 0;
  background: transparent;
  max-width: none;
  max-height: none;
  width: 100%;
  height: 100%;
}

#gwofySpTipsDialog::backdrop {
  background: rgba(0, 0, 0, 0.5);
}

#gwofy-tips-dialog-wrap.gwofy-dialog-wrap {
  transition: opacity 0.35s;
  opacity: 0;
  pointer-events: none;
  display: flex;
  align-items: center;
  position: fixed;
  inset: 0;
  justify-content: center;
  z-index: 999999999999;
  max-width: 1920px;
  box-sizing: border-box;
  padding: 0 10vw;
  color: #000;
  font-family: "San Francisco Text", Helvetica, Arial, sans-serif;
}

#gwofy-tips-dialog-wrap.gwofy-dialog-wrap.show {
  pointer-events: auto;
  opacity: 1;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-panel {
  transition: transform 0.35s;
  position: relative;
  z-index: 100;
  flex: 1;
  width: max-content;
  max-width: 554px;
  background: transparent;
  padding: 0 0 42px;
  color: #5d636f;
  font-size: 12px;
  line-height: normal;
  transform: translateY(120vh);
  box-sizing: content-box;
}

#gwofy-tips-dialog-wrap.gwofy-dialog-wrap.show .gwofy-dialog-panel {
  transform: translateY(0);
}

#gwofy-tips-dialog-wrap .gwofy-dialog-hd {
  position: relative;
  min-height: 146px;
  box-sizing: border-box;
  padding-bottom: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-position: center top;
  background-size: 100% auto;
  background-repeat: no-repeat;
  background-color: #677adf;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-hd-title {
  color: #ecedf3;
  font-size: 32px;
  padding: 0 40px;
  font-weight: 700;
  font-style: italic;
  mix-blend-mode: overlay;
  text-align: center;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-hd-line {
  white-space: nowrap;
  margin: 0;
  line-height: normal;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-bd {
  background-color: #fcfcfc;
  background-position: center -250px;
  background-size: 100% auto;
  background-repeat: no-repeat;
  border-radius: 24px;
  padding: 30px 42px 42px;
  margin-top: -20px;
  position: relative;
  z-index: 1;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-bd-title {
  font-size: 36px;
  text-align: left;
  color: #1d1d1f;
  line-height: normal;
  font-weight: 600;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-bd-title-line,
#gwofy-tips-dialog-wrap .gwofy-dialog-lead {
  margin: 0;
  line-height: normal;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-dr,
#gwofy-tips-dialog-wrap .gwofy-dialog-section {
  margin-top: 30px;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-dr:first-of-type,
#gwofy-tips-dialog-wrap .gwofy-dialog-section:first-of-type {
  margin-top: 30px;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-dt,
#gwofy-tips-dialog-wrap .gwofy-dialog-section-title {
  color: #1d1d1f;
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 16px;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-dd,
#gwofy-tips-dialog-wrap .gwofy-dialog-list-item {
  position: relative;
  padding: 0 0 0 34px;
  margin: 4px 0;
  color: #6e6e73;
  font-size: 16px;
  font-weight: 400;
  line-height: normal;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-dd:after,
#gwofy-tips-dialog-wrap .gwofy-dialog-list-item:before {
  content: "";
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  left: 10px;
  width: 4px;
  height: 4px;
  background: #1d1d1f;
  border-radius: 50%;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-actions {
  margin: 36px 0 0;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-btn.cover {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 56px;
  border: none;
  border-radius: 12px;
  box-sizing: border-box;
  cursor: pointer;
  color: #ecedf3;
  background: linear-gradient(176deg, #677adf 0%, #2630c3 100%);
  text-decoration: none;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
  margin: 0;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-btn.cover:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-legal {
  text-align: center;
  color: #6e6e73;
  font-size: 16px;
  line-height: normal;
  margin: 18px 0 0;
  cursor: default;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-legal-link {
  color: #6e6e73;
  text-decoration: underline;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-close {
  position: absolute;
  right: 12px;
  top: 12px;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 28px;
  line-height: 36px;
  color: #ecedf3;
  padding: 0;
  z-index: 2;
  mix-blend-mode: difference;
}

#gwofy-tips-dialog-wrap .gwofy-dialog-mask {
  position: fixed;
  inset: 0;
  z-index: -1;
  background: rgba(0, 0, 0, 0.5);
}

@media (max-width: 650px) {
  #gwofy-tips-dialog-wrap .gwofy-dialog-panel {
    max-width: 340px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-hd {
    min-height: 100px;
    padding-bottom: 20px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-hd-title {
    font-size: 18px;
    padding: 0 24px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-hd-line {
    white-space: normal;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-close {
    width: 20px;
    height: 20px;
    font-size: 18px;
    line-height: 20px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-bd {
    border-radius: 13px;
    padding: 12px 24px 24px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-bd-title {
    font-size: 20px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-dt,
  #gwofy-tips-dialog-wrap .gwofy-dialog-section-title {
    font-size: 15px;
    margin-bottom: 8px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-dd,
  #gwofy-tips-dialog-wrap .gwofy-dialog-list-item {
    padding-left: 16px;
    font-size: 11px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-dd:after,
  #gwofy-tips-dialog-wrap .gwofy-dialog-list-item:before {
    left: 4px;
    width: 2px;
    height: 2px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-dr,
  #gwofy-tips-dialog-wrap .gwofy-dialog-section {
    margin-top: 12px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-legal {
    font-size: 12px;
    margin-top: 10px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-btn.cover {
    height: 36px;
    font-size: 12px;
  }
  #gwofy-tips-dialog-wrap .gwofy-dialog-actions {
    margin-top: 20px;
  }
}
`,
      widgetExtra: "", // config
      tipsDialogExtra: "", // config
    };
  }

  g.__gwofy_build_default_styles__ = buildDefaultGwofyStyles;

  // ---------------------------------------------------------------------------
  // GWOFY_CONFIG
  // ---------------------------------------------------------------------------

  g.GWOFY_CONFIG = Object.assign(
    { styles: buildDefaultGwofyStyles() },
    /*__GWOFY_CONFIG_JSON__*/
  );

  // ---------------------------------------------------------------------------
  // 配置归一化（兼容旧版 GWOFY_CONFIG 字段名）
  // ---------------------------------------------------------------------------

  /** 合并 cfg.text 为算价用 text 结构 */
  function resolveText(cfg) {
    if (cfg.text && cfg.text.sp) return cfg.text;
    var leg = cfg.textConfig || {};
    return {
      sp: {
        title: leg.SPTextConfigTitle || "",
        desc: leg.SPTextConfigDesc || "",
      },
    };
  }

  /** 支持的展示货币列表 */
  function resolveSupportedCurrencies(cfg) {
    return cfg.supportedCurrencies || cfg.xmhSupportCurrency || [];
  }

  /** 支持的 locale 列表 */
  function resolveSupportedLocales(cfg) {
    return cfg.supportedLocales || cfg.xmhSupportLocale || [];
  }

  /** 解析 hardMaxAmount 数值 */
  function resolveHardMaxAmount(p) {
    if (p.hardMaxAmount != null) return p.hardMaxAmount;
    if (p.CONST_MAX_AMOUNT != null) return p.CONST_MAX_AMOUNT;
    return "0";
  }

  // ---------------------------------------------------------------------------
  // 运行时全局
  // ---------------------------------------------------------------------------

  g.calcScope = "client";
  g.__gwofy_calc_js_cdn_version = "1.0.0";

  /** 构建 __gwofy_calculate__currency__ */
  function buildCurrencyMap() {
    var table = (g.GWOFY_CONFIG && g.GWOFY_CONFIG.currencySymbols) || {};
    var map = { 0: { CNameEn: "None", CSymbol: "None" } };
    var id = 1;
    Object.keys(table).forEach(function (code) {
      map[id] = table[code];
      id += 1;
    });
    g.__gwofy_calculate__currency__ = map;
    return map;
  }

  /**
   * SP 行识别：购物车行 product handle 与 productHandle 一致即为 SP。
   * handle 来自 line.handle / line.product_handle（cart.js、Liquid 快照）。
   */
  g.__gwofy_isspItem__ = function (handle) {
    handle = String(handle || "").trim();
    if (!handle) return false;
    var d = g.__gwofy_calculate_data__ || {};
    var spHandle =
      d.spProductHandle ||
      g.__gwofy_handle__ ||
      (g.GWOFY_CONFIG && g.GWOFY_CONFIG.productHandle) ||
      "";
    return !!(spHandle && handle === spHandle);
  };

  // ---------------------------------------------------------------------------
  // 高精度小数（须在 __gwofy_sync_calculate_data__ 之前，首屏 sync 会用到）
  // ---------------------------------------------------------------------------

  var decimal = {
    add: function (a, b) {
      var r1 = 0, r2 = 0;
      try { r1 = a.toString().split(".")[1].length; } catch (e) {}
      try { r2 = b.toString().split(".")[1].length; } catch (e) {}
      var m = Math.pow(10, Math.max(r1, r2));
      return (a * m + b * m) / m;
    },
    mul: function (a, b) {
      var n = 0, s1 = a.toString(), s2 = b.toString();
      try { n += s1.split(".")[1].length; } catch (e) {}
      try { n += s2.split(".")[1].length; } catch (e) {}
      return (Number(s1.replace(".", "")) * Number(s2.replace(".", ""))) / Math.pow(10, n);
    },
    div: function (a, b) {
      var n1 = 0, n2 = 0;
      try { n1 = a.toString().split(".")[1].length; } catch (e) {}
      try { n2 = b.toString().split(".")[1].length; } catch (e) {}
      var o = Number(a.toString().replace(".", ""));
      var i = Number(b.toString().replace(".", ""));
      return (o / i) * Math.pow(10, n2 - n1);
    },
  };

  /** Shopify.currency.rate 或 1 */
  function shopifyRate() {
    if (typeof Shopify !== "undefined" && Shopify.currency && Shopify.currency.rate) {
      return parseFloat(Shopify.currency.rate) || 1;
    }
    return 1;
  }

  /** 呈现币口径保额：店铺 spMax/spMin × Shopify.currency.rate */
  function resolveTrueSpCoverage(baseYuan) {
    return String(decimal.mul(parseFloat(baseYuan || 0), shopifyRate()));
  }

  /** GWOFY_CONFIG → window 算价/授权快照 */
  g.__gwofy_sync_calculate_data__ = function () {
    var cfg = g.GWOFY_CONFIG || {};
    var p = cfg.pricing || {};
    var curMap = buildCurrencyMap();

    var supportedCurrencies = resolveSupportedCurrencies(cfg);
    var shopifyCurrencyList = supportedCurrencies.map(function (code) {
      var row = cfg.currencySymbols && cfg.currencySymbols[code];
      return row ? row.CNameEn : code;
    });

    g.__gwofy_auth__ = {
      isOpenForSP: cfg.auth ? cfg.auth.isOpenForSP !== false : true,
    };
    g.__gwofy_isCartDefaultOpen = !!cfg.isCartDefaultOpen;
    g.__gwofy_shopId__ = String(cfg.shopId || "");
    g.__gwofy_handle__ = cfg.productHandle || "";
    g.shopifyPluginVersion = cfg.shopifyPluginVersion != null ? cfg.shopifyPluginVersion : 1;
    g.__gwofy_debug_mode__ = !!cfg.debug;
    g.__gwofy_sp_disable_check__ = !!cfg.spDisableCheck;

    g.__gwofy_calculate_data__ = {
      assetUrls: {
        spMore: (cfg.assets && cfg.assets.spMoreUrl) || "",
        spService: (cfg.assets && cfg.assets.spServiceUrl) || "",
      },
      spProductHandle: cfg.productHandle || "",
      hardMaxAmount: String(resolveHardMaxAmount(p)),
      calcRate: String(p.calcRate != null ? p.calcRate : "0"),
      text: resolveText(cfg),
      shopifyCurrencyList: shopifyCurrencyList,
      spConfig: {
        spMaxCoverage: String(p.spMaxCoverage != null ? p.spMaxCoverage : "0"),
        spMinCoverage: String(p.spMinCoverage != null ? p.spMinCoverage : "0"),
        spGreaterMaxCoverAgeTip: p.spGreaterMaxCoverAgeTip || "",
        spBelowMinCoverageTip: p.spBelowMinCoverageTip || "",
        trueSpMaxCoverage: resolveTrueSpCoverage(p.spMaxCoverage),
        trueSpMinCoverage: resolveTrueSpCoverage(p.spMinCoverage),
      },
      supportedCurrencies: supportedCurrencies.slice(),
      supportedLocales: resolveSupportedLocales(cfg).slice(),
      spMeta: cfg.spVersion || {},
      copy: cfg.copy || {},
      currencySymbols: {},
    };

    var data = g.__gwofy_calculate_data__;
    supportedCurrencies.forEach(function (code) {
      if (cfg.currencySymbols && cfg.currencySymbols[code]) {
        data.currencySymbols[code] = cfg.currencySymbols[code].CSymbol;
      }
    });
  };

  /** 货币符号查找 */
  function currencySymbol(code) {
    var data = g.__gwofy_calculate_data__;
    if (data.currencySymbols && data.currencySymbols[code]) {
      return data.currencySymbols[code];
    }
    var table = (g.GWOFY_CONFIG && g.GWOFY_CONFIG.currencySymbols) || {};
    return (table[code] && table[code].CSymbol) || "$";
  }

  g.__gwofy_currency_symbol__ = currencySymbol;

  /** 购物车/Shopify 当前展示货币 */
  function getPresentmentCurrency(cartJson) {
    if (typeof Shopify !== "undefined" && Shopify.currency && Shopify.currency.active) {
      return Shopify.currency.active;
    }
    if (cartJson && cartJson.currency) return cartJson.currency;
    var loc = (typeof window !== "undefined" && window.GWOFY_LOCALIZATION) || {};
    return loc.currency || "USD";
  }

  /** 当前展示 locale */
  function getPresentmentLocale() {
    if (typeof Shopify !== "undefined" && Shopify.locale) {
      return Shopify.locale;
    }
    var loc = (typeof window !== "undefined" && window.GWOFY_LOCALIZATION) || {};
    if (loc.language && loc.country) {
      return loc.language + "-" + loc.country;
    }
    if (loc.language) return loc.language;
    if (typeof document !== "undefined" && document.documentElement) {
      return document.documentElement.lang || undefined;
    }
    return undefined;
  }

  /** 按 money_format 格式化 */
  function formatWithShopMoneyFormat(cents, formatString) {
    var centsNum = Math.round(Number(cents) || 0);
    if (!formatString) return null;

    if (
      typeof Shopify !== "undefined" &&
      typeof Shopify.formatMoney === "function"
    ) {
      return Shopify.formatMoney(centsNum, formatString);
    }

    var placeholderRegex = /\{\{\s*(\w+)\s*\}\}/;
    var match = formatString.match(placeholderRegex);
    if (!match) return null;

    function withDelimiters(number, precision, thousands, decimal) {
      precision = precision == null ? 2 : precision;
      thousands = thousands || ",";
      decimal = decimal || ".";
      if (isNaN(number) || number == null) return "0";
      var fixed = (number / 100).toFixed(precision);
      var parts = fixed.split(".");
      var dollars = parts[0].replace(
        /(\d)(?=(\d\d\d)+(?!\d))/g,
        "$1" + thousands
      );
      return parts[1] ? dollars + decimal + parts[1] : dollars;
    }

    var value;
    switch (match[1]) {
      case "amount_no_decimals":
        value = withDelimiters(centsNum, 0);
        break;
      case "amount_with_comma_separator":
        value = withDelimiters(centsNum, 2, ".", ",");
        break;
      case "amount_no_decimals_with_comma_separator":
        value = withDelimiters(centsNum, 0, ".", ",");
        break;
      case "amount_with_space_separator":
        value = withDelimiters(centsNum, 2, " ", ",");
        break;
      case "amount_no_decimals_with_space_separator":
        value = withDelimiters(centsNum, 0, " ", ",");
        break;
      case "amount_with_apostrophe_separator":
        value = withDelimiters(centsNum, 2, "'", ".");
        break;
      default:
        value = withDelimiters(centsNum, 2);
    }
    return formatString.replace(placeholderRegex, value);
  }

  /** 呈现币金额（元）→ 与主题 .money / 挂件价格一致的字符串 */
  function formatPresentmentMoneyYuan(amountYuan, currency) {
    var cents = Math.round(decimal.mul(parseFloat(amountYuan) || 0, 100));
    var cur = currency || "USD";
    var loc = (typeof window !== "undefined" && window.GWOFY_LOCALIZATION) || {};
    var moneyFormat = loc.moneyFormat;

    if (moneyFormat) {
      var shopFormatted = formatWithShopMoneyFormat(cents, moneyFormat);
      if (shopFormatted != null) return shopFormatted;
    }

    try {
      return new Intl.NumberFormat(getPresentmentLocale(), {
        style: "currency",
        currency: cur,
      }).format(cents / 100);
    } catch (e) {
      var amount = cents / 100;
      var dec = cents % 100 === 0 && cents >= 10000 ? 0 : 2;
      return currencySymbol(cur) + amount.toFixed(dec);
    }
  }

  /** 分 → 呈现币字符串（供 storefront 挂件/结账复用） */
  g.__gwofy_format_shop_money__ = function (cents, currency) {
    return formatPresentmentMoneyYuan(decimal.div(Number(cents) || 0, 100), currency);
  };

  /** 限额提示中的保额金额格式化 */
  function formatLimitCoverageAmount(amountYuan, currency) {
    return formatPresentmentMoneyYuan(amountYuan, currency);
  }

  function escapeTemplateText(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  g.__gwofy_interpolate_template__ = function (template, slots, options) {
    options = options || {};
    var mode = options.mode === "html" ? "html" : "text";
    var re = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g;
    var src = String(template || "");
    var out = "";
    var last = 0;
    var m;
    slots = slots || {};
    while ((m = re.exec(src)) !== null) {
      var chunk = src.slice(last, m.index);
      out += mode === "html" ? escapeTemplateText(chunk) : chunk;
      var key = m[1];
      var val = slots[key] != null ? slots[key] : slots[key.toLowerCase()];
      out += val != null ? String(val) : "";
      last = m.index + m[0].length;
    }
    var tail = src.slice(last);
    out += mode === "html" ? escapeTemplateText(tail) : tail;
    return out;
  };

  function formatSpLimitTip(template, amountFormatted) {
    var tpl = String(template || "");
    if (!tpl) return String(amountFormatted || "");
    if (/\{\{\s*amount\s*\}\}/i.test(tpl)) {
      return g.__gwofy_interpolate_template__(tpl, { amount: amountFormatted }, {
        mode: "text",
      });
    }
    return tpl + amountFormatted;
  }

  /** cart.js 转算价 OrderInfo */
  function cartToOrder(cartJson) {
    var data = g.__gwofy_calculate_data__;
    var items = (cartJson && cartJson.items) || [];
    var totalCents = 0;
    var valid = 0;
    var currency =
      (typeof Shopify !== "undefined" &&
        Shopify.currency &&
        Shopify.currency.active) ||
      (cartJson && cartJson.currency) ||
      "USD";
    var lineItems = [];

    for (var i = 0; i < items.length; i++) {
      var line = items[i];
      if (g.__gwofy_isspItem__(line.handle || line.product_handle)) {
        continue;
      }
      if (!line.requires_shipping) continue;
      if (line.gift_card) continue;
      totalCents = decimal.add(totalCents, line.final_line_price);
      valid += 1;
      lineItems.push({
        product_id: String(line.product_id),
        variant_id: String(line.variant_id),
        sku: line.sku,
        title: line.title,
        final_price: line.final_price,
        quantity: line.quantity,
      });
    }

    return {
      OrderInfo: {
        Currency: currency,
        CurrencySymbol: currencySymbol(currency),
        TotalPayPrice: decimal.div(totalCents, 100),
        ItemValidNum: valid,
      },
      Items: lineItems,
      originalCartItems: items,
    };
  }

  /** SP 商品变体最高价（分） */
  function getMaxSpVariantPriceCents(shopifyProductInfo) {
    var variants = (shopifyProductInfo && shopifyProductInfo.variants) || [];
    var max = 0;
    for (var i = 0; i < variants.length; i++) {
      var p = Number(variants[i].price) || 0;
      if (p > max) max = p;
    }
    return max;
  }

  /** 保费是否超过最贵变体售价 */
  function feeExceedsMaxSpVariantPrice(feeYuan, shopifyProductInfo) {
    if (feeYuan == null || isNaN(feeYuan)) return false;
    var maxCents = getMaxSpVariantPriceCents(shopifyProductInfo);
    if (maxCents <= 0) return true;
    var targetCents = Math.round(decimal.mul(feeYuan, 100));
    return targetCents > maxCents;
  }

  /**
   * SP 变体阶梯能覆盖的订单可保金额上限（呈现币）：
   * 最高变体售价 ÷ calcRate（保费 = 可保金额 × rate 不得超过最贵变体）
   */
  function maxOrderCoverageBySpVariants(shopifyProductInfo, calcRate) {
    var maxSpYuan = decimal.div(
      getMaxSpVariantPriceCents(shopifyProductInfo),
      100
    );
    var rate = parseFloat(calcRate);
    if (!rate || rate <= 0 || isNaN(rate)) return maxSpYuan;
    return decimal.div(maxSpYuan, rate);
  }

  /** 保费匹配 SP 变体 */
  function matchVariant(feeYuan, shopifyProductInfo) {
    var variants = (shopifyProductInfo && shopifyProductInfo.variants) || [];
    if (!variants.length) {
      return {
        totalPrice: "0",
        totalPriceInt: 0,
        extId: "0",
        variantTitle: "",
        variantSku: "",
        variantPriceCents: 0,
        targetFeeCents: 0,
      };
    }

    if (feeYuan === 0) {
      var v0 = variants[0];
      var p0 = v0.price;
      var d0 = p0 >= 10000 ? 0 : 2;
      return {
        totalPrice: decimal.div(p0, 100).toFixed(d0),
        totalPriceInt: p0,
        extId: String(v0.id),
        variantTitle: v0.title || "",
        variantSku: v0.sku || "",
        variantPriceCents: p0,
        targetFeeCents: 0,
      };
    }

    var targetCents = Math.round(decimal.mul(feeYuan, 100));
    var sorted = variants.slice().sort(function (a, b) {
      return a.price - b.price;
    });
    var picked = sorted[sorted.length - 1];
    for (var j = 0; j < sorted.length; j++) {
      var vp = sorted[j].price;
      if (vp >= targetCents) {
        picked = sorted[j];
        break;
      }
      picked = sorted[j];
    }
    var price = picked.price;
    var dec = price >= 10000 ? 0 : 2;
    return {
      totalPrice: decimal.div(price, 100).toFixed(dec),
      totalPriceInt: price,
      extId: String(picked.id),
      variantTitle: picked.title || "",
      variantSku: picked.sku || "",
      variantPriceCents: price,
      targetFeeCents: targetCents,
    };
  }

  /** 构建算价调试 trace */
  function buildCalcTrace(orderParam, calcOut, traceMeta) {
    var cr = (calcOut && calcOut.calcResult) || {};
    var order = orderParam.OrderInfo || {};
    var meta = traceMeta || {};
    var trace = {
      order: {
        currency: order.Currency,
        currencySymbol: order.CurrencySymbol,
        totalPayPrice: order.TotalPayPrice,
        totalPayPriceCents: Math.round(
          decimal.mul(order.TotalPayPrice || 0, 100)
        ),
        itemValidNum: order.ItemValidNum,
        eligibleLineCount: (orderParam.Items || []).length,
      },
      pricing: {
        calcRate: meta.calcRate,
        feeYuan: meta.feeYuan,
        feeCents: meta.feeYuan != null ? Math.round(decimal.mul(meta.feeYuan, 100)) : null,
        shopifyCurrencyRate: shopifyRate(),
        minCoverage: meta.minC,
        maxCoverage: meta.maxC,
        maxSpPriceCents: meta.maxSpPriceCents,
        maxSpPriceYuan: meta.maxSpPriceYuan,
      },
      spMatch: {
        extId: cr.extId,
        totalPrice: cr.totalPrice,
        totalPriceInt: cr.totalPriceInt,
        calcState: cr.calcState != null ? cr.calcState : 0,
        variantTitle: cr.variantTitle || "",
        variantSku: cr.variantSku || "",
        variantPriceCents: cr.variantPriceCents,
        targetFeeCents: cr.targetFeeCents,
      },
    };
    return trace;
  }

  /** 核心算价（费率、限额、变体匹配） */
  function runCalc(orderParam, shopifyProductInfo) {
    var cfg = g.__gwofy_calculate_data__;
    var rate = shopifyRate();
    var cur = orderParam.OrderInfo.Currency;
    var sym = currencySymbol(cur);
    var total = orderParam.OrderInfo.TotalPayPrice;
    var minC = parseFloat(cfg.spConfig.trueSpMinCoverage || 0);
    var maxC = parseFloat(cfg.spConfig.trueSpMaxCoverage || 0);

    var traceMeta = {
      calcRate: parseFloat(cfg.calcRate || 0),
      feeYuan: null,
      minC: minC,
      maxC: maxC,
    };

    if (total > maxC || total < minC) {
      return {
        calcResult: {
          currency: cur,
          currencySymbol: sym,
          totalPrice: "0",
          totalPriceInt: 0,
          extId: "0",
          calcState: total > maxC ? 801027 : 801028,
          disComputeId: "",
        },
        traceMeta: traceMeta,
      };
    }

    if (total === 0 || orderParam.OrderInfo.ItemValidNum === 0) {
      return {
        calcResult: {
          currency: cur,
          currencySymbol: sym,
          totalPrice: "0",
          totalPriceInt: 0,
          extId: "0",
          calcState: 801028,
          disComputeId: "",
        },
        traceMeta: traceMeta,
      };
    }

    var calcRate = traceMeta.calcRate;
    var fee = decimal.mul(total, calcRate);
    traceMeta.feeYuan = fee;
    traceMeta.maxSpPriceCents = getMaxSpVariantPriceCents(shopifyProductInfo);
    traceMeta.maxSpPriceYuan = decimal.div(traceMeta.maxSpPriceCents, 100);

    /* 除 maxC 外：订单可保金额 × rate 不得超过 SP 最高变体售价 */
    if (feeExceedsMaxSpVariantPrice(fee, shopifyProductInfo)) {
      return {
        calcResult: {
          currency: cur,
          currencySymbol: sym,
          totalPrice: "0",
          totalPriceInt: 0,
          extId: "0",
          calcState: 801027,
          disComputeId: "",
          computeMsg: "fee exceeds max sp variant price",
        },
        traceMeta: traceMeta,
      };
    }

    var matched = matchVariant(fee, shopifyProductInfo);
    matched.currency = cur;
    matched.currencySymbol = sym;
    matched.calcState = 0;
    matched.disComputeId = "";

    return {
      calcResult: matched,
      traceMeta: traceMeta,
    };
  }

  /** 算价结果转 storefront computeResult */
  function toComputeOutput(calcOut, orderParam) {
    var cr = calcOut.calcResult;
    var base = {
      disableCheck: !!g.__gwofy_sp_disable_check__,
      disComputeId: cr.disComputeId || "",
      computeState: cr.calcState != null ? cr.calcState : 0,
      computeMsg: cr.computeMsg || "",
      currency: cr.currency,
      currencySymbol: cr.currencySymbol,
      totalPrice: cr.totalPrice,
      totalPriceInt: cr.totalPriceInt,
      extId: cr.extId,
    };
    var out = { computeResult: base };
    if (orderParam) {
      out.calcTrace = buildCalcTrace(
        orderParam,
        calcOut,
        calcOut.traceMeta
      );
    }
    return out;
  }

  /** 算价入口（cart + product） */
  g.__gwofy_calculate__ = function (input) {
    input = input || {};
    try {
      var param = cartToOrder(input.cartJson || { items: [] });
      if (
        param.OrderInfo.TotalPayPrice === 0 ||
        param.OrderInfo.ItemValidNum === 0
      ) {
        var emptyOut = {
          calcResult: {
            currency: param.OrderInfo.Currency,
            currencySymbol: param.OrderInfo.CurrencySymbol,
            totalPrice: "0",
            totalPriceInt: 0,
            extId: "0",
            calcState: 801028,
            disComputeId: "",
            computeMsg: "cart is null or no valid item",
          },
          traceMeta: {
            calcRate: parseFloat(
              (g.__gwofy_calculate_data__ &&
                g.__gwofy_calculate_data__.calcRate) ||
                0
            ),
            feeYuan: 0,
            minC: null,
            maxC: null,
          },
        };
        return toComputeOutput(emptyOut, param);
      }
      return toComputeOutput(
        runCalc(param, input.shopifyProductInfo),
        param
      );
    } catch (err) {
      console.error("[Gwofy Guard] calculate error", err);
      return {
        computeResult: {
          disComputeId: "",
          computeState: 500,
          computeMsg: "calc error",
          currency: "",
          currencySymbol: "",
          totalPrice: "0",
          totalPriceInt: 0,
          extId: "0",
        },
      };
    }
  };

  /**
   * SP 可售限额判断（storefront.checkLimitSp → state.spLimit）。
   * 返回 showBoard：false 时隐藏挂件；tips 为 sp*Tip 模板（{{ amount }}），不替换 text.sp.desc。
   */
  g.__gwofy_calculate_limit_sp__ = function (cartJson, shopifyProductInfo) {
    var param = cartToOrder(cartJson || { items: [] });
    var total = param.OrderInfo.TotalPayPrice;
    var currency = param.OrderInfo.Currency || getPresentmentCurrency(cartJson);
    var sp = g.__gwofy_calculate_data__.spConfig;
    var min = parseFloat(sp.trueSpMinCoverage || 0);
    var max = parseFloat(sp.trueSpMaxCoverage || 0);
    var calcRate = parseFloat(
      (g.__gwofy_calculate_data__ && g.__gwofy_calculate_data__.calcRate) || 0
    );

    if (total < min) {
      return {
        showBoard: false,
        ok: false,
        tips: formatSpLimitTip(
          sp.spBelowMinCoverageTip,
          formatLimitCoverageAmount(min, currency)
        ),
        priceRange: "negative",
      };
    }
    if (total > max) {
      return {
        showBoard: false,
        ok: false,
        tips: formatSpLimitTip(
          sp.spGreaterMaxCoverAgeTip,
          formatLimitCoverageAmount(max, currency)
        ),
        priceRange: "premium",
      };
    }
    if (
      total > 0 &&
      shopifyProductInfo &&
      feeExceedsMaxSpVariantPrice(decimal.mul(total, calcRate), shopifyProductInfo)
    ) {
      var maxOrderCoverage = maxOrderCoverageBySpVariants(
        shopifyProductInfo,
        calcRate
      );
      /* 与 spMaxCoverage 取较小值，提示口径与 total > max 分支一致 */
      if (max > 0 && maxOrderCoverage > max) {
        maxOrderCoverage = max;
      }
      return {
        showBoard: false,
        ok: false,
        tips: formatSpLimitTip(
          sp.spGreaterMaxCoverAgeTip,
          formatLimitCoverageAmount(maxOrderCoverage, currency)
        ),
        priceRange: "premium",
      };
    }
    return { showBoard: true, ok: true, tips: "", priceRange: "standard" };
  };

  /** 可保商品行金额合计（旧 limit） */
  g.__gwofy_calculate_limit__ = function (cartJson) {
    var items = (cartJson && cartJson.items) || [];
    var data = g.__gwofy_calculate_data__;
    var sum = 0;
    for (var i = 0; i < items.length; i++) {
      var e = items[i];
      if (g.__gwofy_isspItem__(e.handle || e.product_handle)) {
        continue;
      }
      if (!e.requires_shipping || e.gift_card) continue;
      sum = decimal.add(sum, e.final_line_price);
    }
    return sum > decimal.mul(data.hardMaxAmount || "0", 100);
  };

  /** 是否开放 SP（对标 authForSp） */
  g.__gwofy_has_sp_auth__ = function () {
    return !!(g.__gwofy_auth__ && g.__gwofy_auth__.isOpenForSP);
  };

  /** 币种 + 语言是否在支持列表（对标 hasSupportCurrencyAndLocale） */
  g.__gwofy_supports_storefront__ = function () {
    if (!g.__gwofy_has_sp_auth__()) return false;
    var data = g.__gwofy_calculate_data__;
    var activeCur =
      (typeof Shopify !== "undefined" &&
        Shopify.currency &&
        Shopify.currency.active) ||
      "";
    var locale =
      (typeof Shopify !== "undefined" && Shopify.locale) ||
      (g.GWOFY_LOCALIZATION && g.GWOFY_LOCALIZATION.language) ||
      "";
    var curList = data.supportedCurrencies || [];
    if (curList.length && curList.indexOf(activeCur) < 0) return false;
    var locList = data.supportedLocales || [];
    if (!locList.length) return true;
    return locList.some(function (row) {
      return (
        row.languageISO === locale ||
        row.languageExtISO === locale ||
        (locale && row.languageExtISO && locale.indexOf(row.languageISO) === 0)
      );
    });
  };

  /** 店面切换国家/货币后按最新 Shopify.currency.rate 重算 trueSp* */
  function installCurrencySyncHook() {
    if (g.__gwofy_currency_sync_hook__) return;
    g.__gwofy_currency_sync_hook__ = true;
    var lastRate = shopifyRate();

    function emitCurrencyChanged() {
      var r = shopifyRate();
      if (r === lastRate) return;
      lastRate = r;
      g.__gwofy_sync_calculate_data__();
      try {
        document.dispatchEvent(
          new CustomEvent("gwofy:currency-changed", { detail: { rate: r } })
        );
      } catch (e) {
        /* IE 等无 CustomEvent */
      }
    }

    document.addEventListener("change", function (ev) {
      var el = ev.target;
      if (!el || !el.matches) return;
      if (
        el.matches('select[name="country_code"]') ||
        el.closest("localization-form") ||
        el.getAttribute("data-currency-selector") != null
      ) {
        setTimeout(emitCurrencyChanged, 250);
      }
    });
  }

  installCurrencySyncHook();

  // ---------------------------------------------------------------------------
  // Bootstrap：内置 GWOFY_CONFIG → remoteScriptUrls → sync → app-storefront.js
  // ---------------------------------------------------------------------------

  function isStorefrontAssetUrl(url) {
    if (!url) return false;
    var path = String(url).split("?")[0];
    return /\/static\/app-storefront\.js$/.test(path);
  }

  function getConfigScriptUrls(cfg) {
    if (!cfg || cfg.remoteScriptUrls == null) return [];
    var raw = cfg.remoteScriptUrls;
    if (typeof raw === "string") {
      var one = raw.trim();
      return one ? [one] : [];
    }
    if (!Array.isArray(raw)) return [];
    return raw
      .map(function (item) {
        return typeof item === "string" ? item.trim() : "";
      })
      .filter(Boolean);
  }

  function partitionConfigScriptUrls(urls) {
    var remote = [];
    var storefront = null;
    urls.forEach(function (url) {
      if (isStorefrontAssetUrl(url)) {
        if (!storefront) storefront = url;
      } else {
        remote.push(url);
      }
    });
    return { remote: remote, storefront: storefront };
  }

  function getRemoteScriptUrls(cfg) {
    return partitionConfigScriptUrls(getConfigScriptUrls(cfg)).remote;
  }

  function resolveStorefrontAssetUrl(cfg) {
    if (g.GWOFY_STOREFRONT_ASSET_URL) return g.GWOFY_STOREFRONT_ASSET_URL;
    return partitionConfigScriptUrls(getConfigScriptUrls(cfg)).storefront;
  }

  function finalizeConfig(remoteLoadedCount) {
    if (!g.GWOFY_CONFIG || typeof g.GWOFY_CONFIG !== "object") {
      console.error("[Gwofy Guard] GWOFY_CONFIG is missing or invalid");
      return;
    }
    g.__gwofy_sync_calculate_data__();
    g.GWOFY_CONFIG_READY = true;
    g.GWOFY_CONFIG_SOURCE =
      remoteLoadedCount > 0 ? "embedded+remote" : "embedded";
    try {
      document.dispatchEvent(
        new CustomEvent("gwofy:config-ready", {
          detail: { source: g.GWOFY_CONFIG_SOURCE },
        })
      );
    } catch (e) {
      /* 无 CustomEvent */
    }
  }

  function loadScript(url) {
    return new Promise(function (resolve, reject) {
      if (!url) {
        reject(new Error("empty script url"));
        return;
      }
      var s = document.createElement("script");
      s.src = url;
      s.async = true;
      var settled = false;
      function settle(err) {
        if (settled) return;
        settled = true;
        if (err) reject(err);
        else resolve();
      }
      s.onload = function () {
        settle();
      };
      s.onerror = function () {
        settle(new Error("script load failed: " + url));
      };
      document.head.appendChild(s);
    });
  }

  var storefrontLoadPromise = null;

  function loadStorefront() {
    if (storefrontLoadPromise) return storefrontLoadPromise;
    var url = resolveStorefrontAssetUrl(g.GWOFY_CONFIG);
    if (!url) {
      console.warn(
        "[Gwofy Guard] storefront script URL missing (GWOFY_STOREFRONT_ASSET_URL or remoteScriptUrls)"
      );
      return Promise.resolve();
    }
    storefrontLoadPromise = loadScript(url)
      .then(function () {
        if (g.GwofyStorefront && typeof g.GwofyStorefront.init === "function") {
          return g.GwofyStorefront.init();
        }
        console.warn("[Gwofy Guard] GwofyStorefront.init missing after load");
      })
      .catch(function (e) {
        console.error("[Gwofy Guard] app-storefront.js load failed", e);
        storefrontLoadPromise = null;
      });
    return storefrontLoadPromise;
  }

  function loadRemoteScripts(urls) {
    var chain = Promise.resolve();
    var loaded = 0;
    urls.forEach(function (url) {
      chain = chain.then(function () {
        return loadScript(url)
          .then(function () {
            loaded += 1;
          })
          .catch(function (err) {
            console.warn("[Gwofy Guard] remote script failed:", url, err);
          });
      });
    });
    return chain.then(function () {
      return loaded;
    });
  }

  function startBootstrap() {
    var urls = getRemoteScriptUrls(g.GWOFY_CONFIG);
    loadRemoteScripts(urls).then(function (loaded) {
      finalizeConfig(loaded);
      if (!g.GWOFY_CONFIG_READY) return;
      loadStorefront();
    });
  }

  g.__gwofy_finalize_config__ = finalizeConfig;
  g.__gwofy_load_storefront__ = loadStorefront;
  g.__gwofy_bootstrap__ = startBootstrap;

  function onDocumentReady() {
    startBootstrap();
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", onDocumentReady);
    } else {
      onDocumentReady();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
