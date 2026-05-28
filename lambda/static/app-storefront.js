/**
 * Gwofy Guard — 店面层（app-storefront.js）
 *
 * 由 app-config.js 在 GWOFY_CONFIG 就绪后动态加载；本文件尽量不随运营配置变更。
 * 职责：挂件 DOM、themeSelectors 挂载、购物车 hook、结账克隆、文案展示（读 GWOFY_CONFIG）。
 *
 * 入口：GwofyStorefront.init()（由 app-config bootstrap 调用，不自动 init）
 */
(function (g) {
  "use strict";
  g = g || (typeof window !== "undefined" ? window : globalThis);

  /** 标记 Gwofy 克隆的结账按钮 */
  var ATTR_CHECKOUT = "data-gwofy-checkout";
  /** 标记「无保障结账」按钮 */
  var ATTR_CHECKOUT_WITHOUT = "data-gwofy-checkout-without";
  /** checkout-plus 文案容器标记 */
  var ATTR_CHECKOUT_MODE = "data-gwofy-checkout-mode";
  /** 克隆按钮内应付额 span 的 data 键（cart.total_price） */
  var ATTR_CHECKOUT_ORIGIN = "data-gwofy-checkout-origin";
  /** 运费险挂件根节点 id */
  var WRAPPER_ID = "gwofyWrapper";
  /** 挂件样式 <style> 元素 id */
  var STYLE_ID = "gwofy-sp-styles";
  /** 说明弹窗样式 <style> 元素 id */
  var TIPS_STYLE_ID = "gwofy-sp-tips-styles";
  /** <dialog> 元素 id */
  var TIPS_DIALOG_ID = "gwofySpTipsDialog";
  /** 弹窗内容外层 wrap id */
  var TIPS_WRAP_ID = "gwofy-tips-dialog-wrap";
  /** 本应用 Cart Ajax 请求标记查询参数 */
  var CART_QS = "_gwofy=1";
  /** 店面 Cart / Section Ajax：显式同源凭证（与 fetch 默认一致，便于 DevTools 核对） */
  var AJAX_CREDENTIALS = "same-origin";
  /** 脚本加载时缓存，避免走 installCartHook 包装层；也避免 mutate 传入的 options */
  var nativeFetch = window.fetch.bind(window);

  // ---------------------------------------------------------------------------
  // 配置
  // ---------------------------------------------------------------------------

  /** 读取并深拷贝 GWOFY_CONFIG，触发 sync calculate data */
  function getConfig() {
    var base = g.GWOFY_CONFIG || {};
    if (typeof g.__gwofy_sync_calculate_data__ === "function") {
      g.__gwofy_sync_calculate_data__();
    }
    var cfg = JSON.parse(JSON.stringify(base));
    cfg.localization = g.GWOFY_LOCALIZATION || {};
    return cfg;
  }

  /** 每次 init / 重算前刷新，避免配置脚本晚于本文件解析 */
  var config = {};

  /** 重新 getConfig 赋给 config */
  function refreshConfig() {
    config = getConfig();
  }

  /**
   * 将 themeSelectors.cartDrawer / cartPage 展平为 storefront 使用的数组。
   * 兼容旧版扁平 widgetAnchors / checkoutBtn / checkoutWithout 写法。
   */
  function resolveThemeSelectors() {
    var ts = (config && config.themeSelectors) || {};
    var resolved = {
      widgetAnchors: [],
      checkoutBtn: [],
      checkoutWithout: [],
      checkoutMode: ts.checkoutMode || "inline",
      checkoutPriceSeparator: ts.checkoutPriceSeparator || " • ",
      checkoutLine:
        ts.checkoutLine || "{{ label }}{{ separator }}{{ price }}",
      checkoutLabel: ts.checkoutLabel || "",
    };

    /** 合并 cartDrawer / cartPage 等块上的锚点与结账选择器 */
    function pushBlock(block) {
      if (!block) return;
      if (block.widgetAnchor) {
        resolved.widgetAnchors.push(block.widgetAnchor);
      }
      if (block.checkoutBtn) {
        var btns = Array.isArray(block.checkoutBtn)
          ? block.checkoutBtn
          : [block.checkoutBtn];
        resolved.checkoutBtn = resolved.checkoutBtn.concat(btns);
      }
      if (block.checkoutWithout) {
        resolved.checkoutWithout.push(block.checkoutWithout);
      }
    }

    pushBlock(ts.cartDrawer);
    pushBlock(ts.cartPage);
    pushBlock(ts.cartPageGotrax);

    if (ts.widgetAnchors && ts.widgetAnchors.length) {
      resolved.widgetAnchors = ts.widgetAnchors;
    }
    if (ts.checkoutBtn && ts.checkoutBtn.length) {
      resolved.checkoutBtn = ts.checkoutBtn;
    }
    if (ts.checkoutWithout && ts.checkoutWithout.length) {
      resolved.checkoutWithout = ts.checkoutWithout;
    }

    return resolved;
  }

  /** HTML 转纯文本 */
  function stripHtml(html) {
    if (!html) return "";
    var el = document.createElement("div");
    el.innerHTML = html;
    return (el.textContent || el.innerText || "").trim();
  }

  /** 读取 text.sp 纯文案（兼容旧 titleHtml / descHtml） */
  function calcTextSp() {
    var data = calcData();
    var sp = (data.text && data.text.sp) || {};
    var title =
      sp.title ||
      (sp.titleHtml ? stripHtml(sp.titleHtml) : "") ||
      "";
    var desc =
      sp.desc ||
      (sp.descHtml ? stripHtml(sp.descHtml) : "") ||
      "";
    if (!title && !desc && data.TextConfig) {
      title = stripHtml(data.TextConfig.SPTextConfigTitle || "");
      desc = stripHtml(data.TextConfig.SPTextConfigDesc || "");
    }
    return { title: title, desc: desc };
  }

  // ---------------------------------------------------------------------------
  // 文案：text.sp 纯文本 + storefront 模板；限额 sp*Tip 只影响 showBoard
  // ---------------------------------------------------------------------------

  /** 弹窗/无障碍等用的纯文本 */
  function widgetCopy() {
    var sp = calcTextSp();
    var plain = calcData().copy || {};
    return {
      title: sp.title || "Shipping Protection",
      subtitle: sp.desc || "",
      checkoutWithout: plain.checkoutWithout || "Checkout without protection",
    };
  }

  /** 挂件配色 white | black */
  function widgetThemeStyle() {
    var style = (config && config.widgetThemeStyle) || "white";
    return style === "black" ? "black" : "white";
  }

  /** 按配色选择标题/说明图标 URL */
  function widgetImageSrc() {
    var assets = (config && config.widgetAssets) || {};
    var isBlack = widgetThemeStyle() === "black";
    return {
      titleIcon: isBlack ? assets.titleIconWhite : assets.titleIconBlack,
      tipsIcon: isBlack ? assets.tipsIconDark : assets.tipsIconLight,
    };
  }

  /** #gwofyWrapper 的 class 字符串 */
  function widgetWrapperClasses() {
    return "gwofy_wrapper gwofy_theme_" + widgetThemeStyle();
  }

  /** spVersion / calculate data 中的 SP 元信息 */
  function spVersionConfig() {
    var data = calcData();
    return data.spMeta || data.SpVersion || config.spVersion || {};
  }

  /** 说明弹窗「了解更多」链接 */
  function spFaqUrl() {
    var sp = spVersionConfig();
    return sp.faqUrl || (config.assets && config.assets.spMoreUrl) || "";
  }

  /** 说明弹窗服务条款链接 */
  function spTermsUrl() {
    var sp = spVersionConfig();
    return sp.terms || "";
  }

  function tipsDialogThemeAttr() {
    var popup = String((spVersionConfig().popup || "SP-DL-5")).trim();
    return popup
      ? ' data-gwofy-dialog-theme="' + escapeHtml(popup) + '"'
      : "";
  }

  function tipsDialogBgStyleProp() {
    var assets = (config && config.tipsDialogAssets) || {};
    var url = assets.bgImage || assets.headerBg || "";
    if (!url) return "";
    return "background-image:url(" + JSON.stringify(String(url)) + ")";
  }

  /** 说明弹窗文案与链接配置 */
  function tipsDialogContent() {
    var td = config.tipsDialog || {};
    var copy = widgetCopy();
    return {
      title: td.title || [copy.title || "Shipping Protection"],
      slogan: td.slogan || [copy.subtitle || ""],
      benefits: td.benefits || [
        {
          title: "Coverage includes：",
          list: [
            "Loss and damage in transit",
            "Theft/Porch Piracy",
            "Mis-delivery by courier",
            "USD$5 compensation for delay",
          ],
        },
        {
          title: "Smooth Claims",
          list: ["One click to file, speedy claims guaranteed."],
        },
      ],
      coverNowText: td.coverNowText || "Cover My Order Now",
      legalLine: td.legalLine || "Visit the {{ faq }} and {{ terms }}",
      faqLabel: td.faqLabel || td.faqText || "FAQ",
      termsLabel: td.termsLabel || td.termsText || "Terms & Conditions",
      faqLink: td.faqLink || spFaqUrl() || "javascript:void(0);",
      termsLink: td.termsLink || spTermsUrl() || "javascript:void(0);",
    };
  }

  /** 转义 HTML 特殊字符 */
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** 调用 config 导出的 __gwofy_interpolate_template__ */
  function gwofyInterpolate(template, slots, mode) {
    if (typeof window.__gwofy_interpolate_template__ === "function") {
      return window.__gwofy_interpolate_template__(template, slots, {
        mode: mode || "text",
      });
    }
    return String(template || "");
  }

  /** 说明弹窗底部 FAQ / 条款一行链接 HTML */
  function buildTipsDialogLegalHtml(c) {
    var linkAttrs =
      ' target="_blank" rel="noopener noreferrer" class="gwofy-dialog-legal-link"';
    var faqAnchor =
      '<a href="' +
      escapeHtml(c.faqLink) +
      '"' +
      linkAttrs +
      ">" +
      escapeHtml(c.faqLabel) +
      "</a>";
    var termsAnchor =
      '<a href="' +
      escapeHtml(c.termsLink) +
      '"' +
      linkAttrs +
      ">" +
      escapeHtml(c.termsLabel) +
      "</a>";
    return gwofyInterpolate(
      c.legalLine,
      { faq: faqAnchor, terms: termsAnchor },
      "html"
    );
  }

  /** 克隆结账按钮 checkoutLine 模板 → 文本前缀/后缀 + 是否用 span.money 展示 {{ price }} */
  function resolveCheckoutLineParts(ts, label, separator, priceText) {
    var tpl = ts.checkoutLine || "{{ label }}{{ separator }}{{ price }}";
    var slots = {
      label: label,
      separator: separator,
      price: priceText,
    };
    var priceRe = /\{\{\s*price\s*\}\}/i;
    var match = tpl.match(priceRe);
    if (!match) {
      return {
        prefix: gwofyInterpolate(tpl, slots, "text"),
        suffix: "",
        usePriceSpan: false,
      };
    }
    var idx = tpl.search(priceRe);
    return {
      prefix: gwofyInterpolate(tpl.slice(0, idx), slots, "text"),
      suffix: gwofyInterpolate(tpl.slice(idx + match[0].length), slots, "text"),
      usePriceSpan: true,
    };
  }

  /** 从 GWOFY_CONFIG.styles 读取 CSS（widget / tipsDialog 为基础样式，各自 widgetExtra / tipsDialogExtra 追加覆盖） */
  function resolveStyleConfig() {
    var styles = (config && config.styles) || {};
    var fallback =
      typeof g.__gwofy_build_default_styles__ === "function"
        ? g.__gwofy_build_default_styles__()
        : { widget: "", widgetExtra: "", tipsDialog: "", tipsDialogExtra: "" };
    return {
      widget: styles.widget || fallback.widget || "",
      widgetExtra: styles.widgetExtra || fallback.widgetExtra || "",
      tipsDialog: styles.tipsDialog || fallback.tipsDialog || "",
      tipsDialogExtra:
        styles.tipsDialogExtra || fallback.tipsDialogExtra || "",
    };
  }

  /** 向 document.head 注入 <style> */
  function injectStyleSheet(styleId, css) {
    if (!css || document.getElementById(styleId)) return;
    var el = document.createElement("style");
    el.id = styleId;
    el.textContent = css;
    document.head.appendChild(el);
  }

  /** 注入说明弹窗 CSS */
  function injectTipsDialogStyles() {
    refreshConfig();
    var sheet = resolveStyleConfig();
    injectStyleSheet(
      TIPS_STYLE_ID,
      sheet.tipsDialog + (sheet.tipsDialogExtra || "")
    );
  }

  /** 说明弹窗 innerHTML（模板字符串，结构对齐 xcotton SP-DL-5） */
  function buildTipsDialogInnerHtml() {
    var c = tipsDialogContent();
    var bgStyle = tipsDialogBgStyleProp();
    var bgAttr = bgStyle ? ' style="' + bgStyle + '"' : "";
    var titleHtml = (c.title || [])
      .map(function (line) {
        return `<div class="gwofy-dialog-hd-line">${escapeHtml(line)}</div>`;
      })
      .join("");
    var sloganLines = (c.slogan || [])
      .filter(Boolean)
      .map(function (line) {
        return `<div class="gwofy-dialog-bd-title-line">${escapeHtml(line)}</div>`;
      })
      .join("");
    var sloganHtml = sloganLines
      ? `<div class="gwofy-dialog-bd-title">${sloganLines}</div>`
      : "";
    var benefitsHtml = (c.benefits || [])
      .map(function (block) {
        var items = (block.list || [])
          .map(function (line) {
            return `<div class="gwofy-dialog-dd">${escapeHtml(line)}</div>`;
          })
          .join("");
        return `<div class="gwofy-dialog-dr"><div class="gwofy-dialog-dt">${escapeHtml(block.title)}</div><div class="gwofy-dialog-dd-list">${items}</div></div>`;
      })
      .join("");
    var legalHtml = buildTipsDialogLegalHtml(c);

    return `
<div id="${TIPS_WRAP_ID}" class="gwofy-dialog-wrap"${tipsDialogThemeAttr()} role="presentation">
  <div class="gwofy-dialog-panel" id="gwofy-dialog-panel" role="dialog" aria-modal="true" aria-labelledby="gwofy-tips-dialog-title">
    <button type="button" class="gwofy-dialog-close" data-gwofy-tips-close aria-label="Close">&times;</button>
    <div class="gwofy-dialog-hd"${bgAttr}>
      <div class="gwofy-dialog-hd-title" id="gwofy-tips-dialog-title">${titleHtml}</div>
    </div>
    <div class="gwofy-dialog-bd"${bgAttr}>
      ${sloganHtml}
      ${benefitsHtml}
      <div class="gwofy-dialog-actions">
        <button type="button" class="gwofy-dialog-btn cover more" data-gwofy-tips-cover>${escapeHtml(c.coverNowText)}</button>
        <div class="gwofy-dialog-legal switch_desc">${legalHtml}</div>
      </div>
    </div>
  </div>
  <div class="gwofy-dialog-mask" data-gwofy-tips-close></div>
</div>`;
  }

  /** 创建/更新 <dialog> 与内容 */
  function ensureTipsDialog() {
    injectTipsDialogStyles();
    var dlg = document.getElementById(TIPS_DIALOG_ID);
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = TIPS_DIALOG_ID;
      document.body.appendChild(dlg);
      bindTipsDialogEvents(dlg);
    }
    dlg.innerHTML = buildTipsDialogInnerHtml().trim();
    return dlg;
  }

  /** 绑定弹窗关闭与遮罩点击 */
  function bindTipsDialogEvents(dlg) {
    if (!dlg || dlg.getAttribute("data-gwofy-tips-bound")) return;
    dlg.setAttribute("data-gwofy-tips-bound", "1");

    dlg.addEventListener("click", function (ev) {
      if (ev.target.closest("[data-gwofy-tips-close]")) {
        ev.preventDefault();
        closeTipsDialog();
        return;
      }
      if (ev.target.closest("[data-gwofy-tips-cover]")) {
        ev.preventDefault();
        onTipsDialogCoverNow();
      }
    });

    dlg.addEventListener("cancel", function (ev) {
      ev.preventDefault();
      closeTipsDialog();
    });

    dlg.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeTipsDialog();
      }
    });
  }

  /** 说明弹窗「立即投保」：可售则加 SP 并进 checkout，否则 alert limit.tips */
  function onTipsDialogCoverNow() {
    if (state.busy) return;
    applyComputeAndUi();
    if (!isSpOfferAvailable()) {
      var limit = state.spLimit || checkLimitSp(state.cart);
      var msg = (limit && limit.tips) || "";
      if (msg) {
        alert(msg);
      } else {
        alert(
          "Shipping Protection is not available for this order at the moment."
        );
      }
      return;
    }

    closeTipsDialog();
    state.busy = true;
    window.__gwofy_switch_touched__ = true;
    state.switchOn = true;
    applyComputeAndUi();
    renderAllWidgets();

    syncSpLineToCompute()
      .then(function () {
        applyComputeAndUi();
        renderAllWidgets();
        var nativeBtn = findNativeCheckoutNear(document.body);
        if (nativeBtn) {
          nativeBtn.click();
        } else {
          window.location.href = "/checkout";
        }
      })
      .catch(function (e) {
        log("tips cover now error", e);
        alert("Unable to add Shipping Protection. Please try again.");
      })
      .finally(function () {
        state.busy = false;
      });
  }

  /** 打开说明弹窗 */
  function openTipsDialog() {
    config = getConfig();
    var dlg = ensureTipsDialog();
    var wrap = dlg.querySelector(".gwofy-dialog-wrap");
    if (wrap) {
      requestAnimationFrame(function () {
        wrap.classList.add("show");
      });
    }
    if (typeof dlg.showModal === "function") {
      try {
        dlg.showModal();
      } catch (e) {
        dlg.setAttribute("open", "open");
      }
    } else {
      dlg.setAttribute("open", "open");
    }
    log("tips dialog open");
  }

  /** 关闭说明弹窗 */
  function closeTipsDialog() {
    var dlg = document.getElementById(TIPS_DIALOG_ID);
    if (!dlg) return;
    var wrap = dlg.querySelector(".gwofy-dialog-wrap");
    if (wrap) wrap.classList.remove("show");
    if (typeof dlg.close === "function") {
      try {
        dlg.close();
      } catch (e2) {
        dlg.removeAttribute("open");
      }
    } else {
      dlg.removeAttribute("open");
    }
  }

  /** debug 模式下 console.log */
  function log() {
    if (!window.__gwofy_debug_mode__) return;
    console.log.apply(console, ["[Gwofy Guard]"].concat([].slice.call(arguments)));
  }

  /** Shopify.routes.root 或 / */
  function shopifyRoot() {
    return (
      (typeof Shopify !== "undefined" && Shopify.routes && Shopify.routes.root) ||
      "/"
    );
  }

  /** 是否开放 SP 且 locale/currency 支持 */
  function supportsStorefront() {
    if (typeof window.__gwofy_supports_storefront__ === "function") {
      return window.__gwofy_supports_storefront__();
    }
    return !!(window.__gwofy_auth__ && window.__gwofy_auth__.isOpenForSP);
  }

  // ---------------------------------------------------------------------------
  // 状态
  // ---------------------------------------------------------------------------

  /** 通用防抖包装 */
  function debounce(fn, ms) {
    var t;
    return function () {
      clearTimeout(t);
      var ctx = this;
      var args = arguments;
      t = setTimeout(function () {
        fn.apply(ctx, args);
      }, ms);
    };
  }

  /** 店面运行时状态 */
  var state = {
    /** 当前购物车 cart.js 结构 */
    cart: null,
    /** SP 商品 /products/{handle}.js 归一化结果 */
    product: null,
    /** 最近一次 __gwofy_calculate__ 的 computeResult */
    compute: null,
    /** __gwofy_calculate_limit_sp__ 返回值（showBoard/tips） */
    spLimit: null,
    /** 用户是否开启运费险（勾选/购物车含 SP 行） */
    switchOn: false,
    /** 限额是否允许操作开关（limit.ok） */
    conditionOk: true,
    /** cart 变更或结账流程进行中 */
    busy: false,
    /** 已挂载挂件锚点记录 */
    mounted: {},
    /** 结账 UI 绑定状态（预留） */
    checkoutBound: false,
    /** Section patch 期间 >0 时禁止 MutationObserver 重挂载 */
    cartUiLock: 0,
    /** renderAllWidgets 缓存，避免重复写 DOM */
    lastWidgetUi: null,
  };

  /** 进入 cart UI 锁（Section patch） */
  function beginCartUiLock() {
    state.cartUiLock += 1;
  }

  /** 退出 cart UI 锁 */
  function endCartUiLock() {
    if (state.cartUiLock > 0) state.cartUiLock -= 1;
  }

  /** 是否处于 cart UI 锁 */
  function isCartUiLocked() {
    return state.cartUiLock > 0;
  }

  /** 购物车是否含应剥离的 SP 行 */
  function cartHasStripLines(cart) {
    if (!cart || !cart.items) return false;
    for (var i = 0; i < cart.items.length; i++) {
      if (shouldStripCartLineItem(cart.items[i])) return true;
    }
    return false;
  }

  /** 购物车是否有非 SP 的普通商品（空车不展示 #gwofyWrapper） */
  function cartHasMerchandise(cart) {
    if (!cart) return false;
    var items = cart.items;
    if (!Array.isArray(items)) {
      return typeof cart.item_count === "number" && cart.item_count > 0;
    }
    for (var i = 0; i < items.length; i++) {
      if (!shouldStripCartLineItem(items[i])) return true;
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // SP 行识别（规则在 gwofy-config.js __gwofy_calculate_data__）
  // ---------------------------------------------------------------------------

  /** window.__gwofy_calculate_data__ */
  function calcData() {
    return window.__gwofy_calculate_data__ || {};
  }

  /** 购物车行 product handle（cart.js / Liquid 快照） */
  function lineProductHandle(line) {
    return (line && (line.handle || line.product_handle)) || "";
  }

  /** 是否 SP 购物车行（与 GWOFY_CONFIG.productHandle 比对） */
  function isSpLine(line) {
    return (
      typeof window.__gwofy_isspItem__ === "function" &&
      window.__gwofy_isspItem__(lineProductHandle(line))
    );
  }

  /** 查找购物车中 SP 行 */
  function findSpLine(cart) {
    if (!cart || !cart.items) return null;
    for (var i = 0; i < cart.items.length; i++) {
      if (isSpLine(cart.items[i])) return cart.items[i];
    }
    return null;
  }

  /** /cart 页（含 /en/cart 等 markets 路径） */
  function isCartPage() {
    return /\/cart\/?(\?|$)/.test(window.location.pathname || "");
  }

  /** 节点是否在 cart 页行列表内 */
  function isOnCartPageList(el) {
    if (!el || !isCartPage()) return false;
    return !!(
      el.closest("#cart") ||
      el.closest("main.cart") ||
      el.closest("form.cart") ||
      el.closest(".cart__items") ||
      el.closest("cart-items") ||
      el.closest("#CartPage") ||
      el.closest(".cart-page")
    );
  }

  /** 向上查找购物车行 DOM */
  function closestCartLineRow(el) {
    if (!el || !el.closest) return null;
    var rowSelectors = [
      ".cart-item",
      "tr.cart-item",
      ".cart__item",
      ".line-item",
      "[data-cart-item]",
      ".product-cart-item",
      "tbody tr[id^='CartItem-']",
    ];
    for (var i = 0; i < rowSelectors.length; i++) {
      var row = el.closest(rowSelectors[i]);
      if (row && isOnCartPageList(row)) return row;
    }
    if (el.id && /^CartItem-\d+$/i.test(el.id) && isOnCartPageList(el)) {
      return el;
    }
    return null;
  }

  /** 按 line 信息定位行 DOM */
  function findCartLineRowElement(line) {
    if (!line) return null;
    var vid = String(line.variant_id || "");
    var key = line.key || "";
    var handle = line.handle || line.product_handle || "";
    var probes = [];

    if (vid) {
      probes.push('[data-variant-id="' + vid + '"]');
      probes.push('a[href*="variant=' + vid + '"]');
      probes.push('a[href*="/variants/' + vid + '"]');
    }
    if (key) {
      probes.push('input[name="updates[' + key + ']"]');
      probes.push('[data-cart-item-key="' + key + '"]');
      probes.push('[data-key="' + key + '"]');
      probes.push('[data-line-key="' + key + '"]');
      probes.push('quantity-input[data-quantity-variant-id="' + vid + '"]');
    }
    if (handle) {
      probes.push('a[href*="/products/' + handle + '"]');
    }

    for (var p = 0; p < probes.length; p++) {
      var nodes;
      try {
        nodes = document.querySelectorAll(probes[p]);
      } catch (e) {
        continue;
      }
      for (var n = 0; n < nodes.length; n++) {
        var row = closestCartLineRow(nodes[n]);
        if (row) return row;
      }
    }
    return null;
  }

  /** 该行是否应从 cart 页 DOM 移除 */
  function shouldStripCartLineItem(line) {
    return isSpLine(line);
  }

  /**
   * 主题 section 重绘后，从 DOM 删掉仍渲染出的 SP 行（cart.js 已无该行时）
   */
  function purgeSpCartLineItemsFromDom() {
    if (!isCartPage()) return;

    if (state.cart && state.cart.items) {
      for (var i = 0; i < state.cart.items.length; i++) {
        var line = state.cart.items[i];
        if (!shouldStripCartLineItem(line)) continue;
        var row = findCartLineRowElement(line);
        if (row && row.parentNode) {
          row.parentNode.removeChild(row);
        }
      }
      return;
    }

    var productHandle =
      config.productHandle ||
      (typeof window !== "undefined" && window.GWOFY_PRODUCT_HANDLE) ||
      "";
    if (productHandle) {
      var links = document.querySelectorAll(
        'a[href*="/products/' + productHandle + '"]'
      );
      for (var j = 0; j < links.length; j++) {
        var orphanRow = closestCartLineRow(links[j]);
        if (orphanRow && orphanRow.parentNode) {
          orphanRow.parentNode.removeChild(orphanRow);
        }
      }
    }
  }

  /** 防抖：从 DOM 移除 SP 购物车行 */
  var schedulePurgeSpCartLines = debounce(purgeSpCartLineItemsFromDom, 80);

  // ---------------------------------------------------------------------------
  // Section Rendering 刷新（对标 xcotton xo / po + cart-items.refresh）
  // ---------------------------------------------------------------------------

  /** Section 刷新时要 patch 的行项选择器 */
  /** Section 刷新时要 patch 的行项选择器 */
  var CART_LINE_PATCH_SELECTORS = [
    ".cart__items",
    ".m-cart__items",
    ".cart-order__summary",
    ".cart-items",
    "cart-items",
    ".t4s-cartPage__items",
    ".Cart__ItemList",
    ".line-item-table__list",
    ".cart-item-list",
  ];

  /** Section 刷新时要 patch 的小计选择器 */
  /** Section 刷新时要 patch 的小计选择器 */
  var CART_TOTAL_PATCH_SELECTORS = [
    ".cart__footer .totals",
    ".cart__footer .cart_total",
    ".m-cart__summary",
    ".cart-form__totals",
    ".cart__total",
    ".totals",
    "#main-cart-footer",
    ".cart__recap-block",
    ".checkout-subtotal-container .subtotal",
  ];

  /** Section HTML 片段转 Document */
  function parseSectionHtml(html) {
    if (!html) return null;
    var trimmed = String(html).trim();
    /* Shopify sections 多为片段，包进 body 以便 querySelector / getElementById */
    if (
      trimmed.toLowerCase().indexOf("<!doctype") !== 0 &&
      trimmed.toLowerCase().indexOf("<html") !== 0
    ) {
      trimmed = `<!DOCTYPE html><html><head></head><body>${trimmed}</body></html>`;
    }
    return new DOMParser().parseFromString(trimmed, "text/html");
  }

  /** 扫描 section 内可 patch 的选择器 */
  function discoverSectionPatches(sectionRootEl) {
    var patches = [];
    var i;
    if (!sectionRootEl) return patches;

    for (i = 0; i < CART_LINE_PATCH_SELECTORS.length; i++) {
      if (sectionRootEl.querySelector(CART_LINE_PATCH_SELECTORS[i])) {
        patches.push(CART_LINE_PATCH_SELECTORS[i]);
        break;
      }
    }
    for (i = 0; i < CART_TOTAL_PATCH_SELECTORS.length; i++) {
      if (sectionRootEl.querySelector(CART_TOTAL_PATCH_SELECTORS[i])) {
        patches.push(CART_TOTAL_PATCH_SELECTORS[i]);
      }
    }
    return patches;
  }

  /** 收集需刷新的 section：/cart 用 main cart section，其它页用 drawer */
  function collectCartSectionRefreshMap() {
    var map = {};
    var nodes = document.querySelectorAll("body [id*='shopify-section']");

    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var sectionId = el.id.replace("shopify-section-", "");
      var rootSelector = "#" + el.id;

      if (isCartPage()) {
        if (
          (sectionId.indexOf("cart") >= 0 &&
            sectionId.indexOf("cart-drawer") < 0) ||
          sectionId.indexOf("main") >= 0
        ) {
          var patches = discoverSectionPatches(el);
          if (patches.length) {
            map[sectionId] = { root: rootSelector, patches: patches };
          }
        }
      } else if (
        sectionId.indexOf("cart-drawer") >= 0 ||
        sectionId.indexOf("mini-cart") >= 0
      ) {
        var drawerPatches = discoverSectionPatches(el);
        if (drawerPatches.length) {
          map[sectionId] = { root: rootSelector, patches: drawerPatches };
        }
      }
    }

    return map;
  }

  /** 请求 ?sections= 获取 section HTML */
  function fetchCartSections(sectionIds) {
    if (!sectionIds.length) return Promise.resolve({});
    var root = shopifyRoot();
    var base = root + (root.slice(-1) === "/" ? "" : "/");
    var url =
      base +
      "?sections=" +
      encodeURIComponent(sectionIds.join(",")) +
      "&_gwofy=1&_=" +
      Date.now();
    return fetchJson(url);
  }

  /** 在解析文档中找 section 根节点 */
  function findSectionRootInDoc(doc, sectionId, rootSelector) {
    if (!doc || !doc.querySelector) return null;
    return (
      doc.querySelector("#shopify-section-" + sectionId) ||
      doc.querySelector(rootSelector)
    );
  }

  /** 用 section 片段更新页面局部 DOM */
  function patchSectionSelector(meta, sectionId, patchSelector, parsedDoc) {
    var liveRoot = document.querySelector(meta.root);
    if (!liveRoot) return;
    var sourceRoot = findSectionRootInDoc(parsedDoc, sectionId, meta.root);
    if (!sourceRoot) return;

    var liveTarget = liveRoot.querySelector(patchSelector);
    var sourceTarget = sourceRoot.querySelector(patchSelector);
    if (!liveTarget || !sourceTarget) return;

    liveTarget.innerHTML = sourceTarget.innerHTML;
  }

  /** 刷新主题 cart section 组件 */
  function refreshNativeCartComponents() {
    var tags = ["cart-items", "cart-drawer-items", "cart-drawer", "m-cart-items"];
    for (var i = 0; i < tags.length; i++) {
      var el =
        document.getElementById(tags[i]) || document.querySelector(tags[i]);
      if (!el) continue;
      if (typeof el.refresh === "function") {
        try {
          el.refresh();
        } catch (e) {
          log("cart component refresh failed", tags[i], e);
        }
      } else if (typeof el.onCartUpdate === "function") {
        try {
          el.onCartUpdate();
        } catch (e2) {
          log("cart component onCartUpdate failed", tags[i], e2);
        }
      }
    }
  }

  /** 切换空购物车 footer 显示 */
  function toggleCartEmptyFooter(cart) {
    if (!isCartPage() || !cart) return;
    var footer = document.getElementById("main-cart-footer");
    if (footer && footer.classList) {
      footer.classList.toggle("is-empty", Number(cart.item_count) === 0);
    }
  }

  /** 用 Section API 重绘 line items / 小计（xcotton forceRefreshUI） */
  function refreshCartLineSections() {
    if (isCartUiLocked()) return Promise.resolve(false);

    var sectionMap = collectCartSectionRefreshMap();
    var sectionIds = Object.keys(sectionMap);
    if (!sectionIds.length) {
      return Promise.resolve(false);
    }

    beginCartUiLock();
    return fetchCartSections(sectionIds)
      .then(function (payload) {
        var patched = false;
        sectionIds.forEach(function (sectionId) {
          var html = payload[sectionId];
          if (!html) return;
          var doc = parseSectionHtml(html);
          if (!doc) return;
          var meta = sectionMap[sectionId];
          meta.patches.forEach(function (patchSelector) {
            patchSectionSelector(meta, sectionId, patchSelector, doc);
            patched = true;
          });
        });
        /* 已 patch section HTML，勿再 cart-items.refresh()，否则会二次拉 section 并触发 Observer 循环 */
        toggleCartEmptyFooter(state.cart);
        return patched;
      })
      .catch(function (e) {
        log("refreshCartLineSections failed", e);
        return false;
      })
      .finally(function () {
        endCartUiLock();
      });
  }

  /** 重新挂载挂件与结账 UI */
  function remountGwofyCartUi() {
    refreshConfig();
    beginCartUiLock();
    try {
      syncStorefrontVisibility();
    } finally {
      endCartUiLock();
    }
  }

  /** /cart 页：API 移除 SP 后 Section 刷新（非隐藏 DOM） */
  function stripSpLinesOnCartPage() {
    if (!isCartPage()) return Promise.resolve(state.cart);

    var hadSpLine = cartHasStripLines(state.cart);
    var chain = hadSpLine ? removeSpLine() : Promise.resolve(state.cart);

    return chain.then(function (cart) {
      state.cart = cart || state.cart;
      if (!hadSpLine) {
        return cart;
      }
      return refreshCartLineSections().then(function () {
        remountGwofyCartUi();
        return cart;
      });
    });
  }

  /** cart 变更后同步 SP 行 */
  function afterCartDataReady() {
    if (isCartPage()) {
      if (cartHasStripLines(state.cart)) {
        return stripSpLinesOnCartPage();
      }
      return Promise.resolve(state.cart);
    }
    if (!isSpOfferAvailable()) {
      if (findSpLine(state.cart)) {
        return removeSpLine();
      }
      return Promise.resolve(state.cart);
    }
    if (state.switchOn) {
      if (isSpLineSyncedWithCompute()) {
        return Promise.resolve(state.cart);
      }
      return syncSpLineToCompute();
    }
    return Promise.resolve(state.cart);
  }

  /**
   * 是否展示 SP 挂件与 checkoutPlus。
   * limit.showBoard 为 false（低于/超过保额、保费超最贵变体等）时不展示；与 descHtml 文案无关。
   */
  function isSpOfferAvailable() {
    var limit = state.spLimit;
    if (!limit || !limit.showBoard) return false;
    if (!state.compute || state.compute.extId === "0") return false;
    if (Number(state.compute.totalPriceInt) <= 0) return false;
    return true;
  }

  /** 调用 config.js 中的算价 API（对标 protection.calculate） */
  function runCalculate() {
    if (!state.cart || !state.product || typeof window.__gwofy_calculate__ !== "function") {
      window.__gwofy_last_calc__ = null;
      return null;
    }
    var out = window.__gwofy_calculate__({
      cartJson: state.cart,
      shopifyProductInfo: state.product,
    });
    window.__gwofy_last_calc__ = out || null;
    logCalcTrace(out);
    return out && out.computeResult ? out.computeResult : null;
  }

  /** 控制台输出订单可保金额与 SP 变体匹配明细 */
  function logCalcTrace(out) {
    if (!out || !out.calcTrace) return;
    var t = out.calcTrace;
    var order = t.order || {};
    var pricing = t.pricing || {};
    var sp = t.spMatch || {};

    if (window.__gwofy_debug_mode__) {
      console.groupCollapsed(
        "[Gwofy Guard] 算价 " +
          (order.currencySymbol || "") +
          order.totalPayPrice +
          " → SP " +
          (sp.totalPrice || "0")
      );
      console.log("订单可保金额", order);
      console.log("费率/保额", pricing);
      console.log("SP 匹配变体", sp);
      console.log("computeResult", out.computeResult);
      console.groupEnd();
    }
  }

  /** 调用 config 限额；tips 不写入挂件描述，仅用于 showBoard / conditionOk */
  function checkLimitSp(cart) {
    if (typeof window.__gwofy_calculate_limit_sp__ !== "function") {
      return { showBoard: true, ok: true, tips: "" };
    }
    return window.__gwofy_calculate_limit_sp__(cart, state.product);
  }

  /** 符号 + 金额字符串 */
  function formatMoney(symbol, amountStr) {
    var moneyLine =
      (config && config.copy && config.copy.moneyLine) ||
      (calcData().copy && calcData().copy.moneyLine) ||
      "{{ symbol }}{{ amount }}";
    return gwofyInterpolate(
      moneyLine,
      { symbol: symbol || "$", amount: amountStr },
      "text"
    );
  }

  /** 货币符号 */
  function getCurrencySymbol(code) {
    if (typeof window.__gwofy_currency_symbol__ === "function") {
      return window.__gwofy_currency_symbol__(code);
    }
    var data = calcData();
    return (data.currencySymbols && data.currencySymbols[code]) || "$";
  }

  /** GWOFY_LOCALIZATION 或 config.localization */
  function getLocalization() {
    return (
      (config && config.localization) ||
      (typeof window !== "undefined" && window.GWOFY_LOCALIZATION) ||
      {}
    );
  }

  /** 店面当前展示货币（Markets 切换后与 cart 一致） */
  function getDisplayCurrency() {
    if (
      typeof Shopify !== "undefined" &&
      Shopify.currency &&
      Shopify.currency.active
    ) {
      return Shopify.currency.active;
    }
    if (state.cart && state.cart.currency) {
      return state.cart.currency;
    }
    return getLocalization().currency || "USD";
  }

  /** BCP 47，用于 Intl 千分位/符号位置 */
  function getDisplayLocale() {
    if (typeof Shopify !== "undefined" && Shopify.locale) {
      return Shopify.locale;
    }
    var loc = getLocalization();
    if (loc.language && loc.country) {
      return loc.language + "-" + loc.country;
    }
    if (loc.language) {
      return loc.language;
    }
    if (typeof document !== "undefined" && document.documentElement) {
      return document.documentElement.lang || undefined;
    }
    return undefined;
  }

  /**
   * 按店铺 money_format 格式化（与主题 .money 一致）
   * 无 money_format 时回退 Intl（currency + 店面 locale）
   */
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

  /** 格式化分金额为店面货币字符串 */
  function formatShopMoney(cents, currencyOverride) {
    var cur = currencyOverride || getDisplayCurrency();
    if (typeof window.__gwofy_format_shop_money__ === "function") {
      return window.__gwofy_format_shop_money__(cents, cur);
    }
    var centsNum = Math.round(Number(cents) || 0);
    var loc = getLocalization();
    var moneyFormat = loc.moneyFormat;

    if (moneyFormat) {
      var shopFormatted = formatWithShopMoneyFormat(centsNum, moneyFormat);
      if (shopFormatted != null) {
        return shopFormatted;
      }
    }

    try {
      return new Intl.NumberFormat(getDisplayLocale(), {
        style: "currency",
        currency: cur,
      }).format(centsNum / 100);
    } catch (e) {
      var amount = centsNum / 100;
      var dec = centsNum % 100 === 0 && centsNum >= 10000 ? 0 : 2;
      return formatMoney(getCurrencySymbol(cur), amount.toFixed(dec));
    }
  }

  /** 结账按钮 / 挂件价格展示（分 → 店面货币字符串） */
  function formatCheckoutDisplayAmount(cents, currency) {
    return formatShopMoney(cents, currency || getDisplayCurrency());
  }

  /** 规范化结账按钮文案 */
  function normalizeCheckoutLabel(text) {
    var t = (text || "").trim().replace(/\s+/g, " ");
    if (!t) return "Checkout";
    if (/^check\s*out$/i.test(t)) return "Checkout";
    return t;
  }

  /** 从原生结账按钮提取标签（去价格） */
  function extractCheckoutLabel(nativeBtn, sep) {
    var ts = resolveThemeSelectors();
    if (ts.checkoutLabel) return ts.checkoutLabel;

    var label = "Checkout";
    if (!nativeBtn) return label;
    var text = (nativeBtn.textContent || "").trim();
    if (!text) return label;
    var head = text.split(sep)[0].trim();
    if (head && !/\d/.test(head)) {
      label = normalizeCheckoutLabel(head);
    }
    return label;
  }

  /**
   * 结账按钮是否应在 cart.total_price 上叠加 SP（未进 cart 时）
   * checkoutPlus：默认展示「含保障」应付额（对标 xcotton updateCheckoutBtnText）
   * 仅当用户主动关掉 SP 开关（__gwofy_switch_touched__ && !switchOn）时不叠加
   */
  function shouldIncludeSpInCheckoutTotal() {
    if (!state.compute || state.compute.extId === "0") {
      return false;
    }
    if (Number(state.compute.totalPriceInt) <= 0) {
      return false;
    }
    if (findSpLine(state.cart)) {
      return false;
    }
    if (window.__gwofy_switch_touched__ && !state.switchOn) {
      return false;
    }
    var ts = resolveThemeSelectors();
    if (ts.checkoutMode === "inline") {
      return true;
    }
    return !!state.switchOn;
  }

  /** SP 费用（分）：行价或 compute */
  function getSpFeeCents() {
    if (!state.compute) return 0;
    var line = findSpLine(state.cart);
    if (line) {
      return Number(line.final_line_price) || 0;
    }
    return Number(state.compute.totalPriceInt) || 0;
  }

  /**
   * checkout-plus 展示总价（分）
   * data-gwofy-checkout-origin 仍为 cart.total_price；.money 为合并后应付额
   */
  function computeCheckoutTotalCents() {
    var cartTotal = (state.cart && state.cart.total_price) || 0;
    if (!shouldIncludeSpInCheckoutTotal()) {
      return cartTotal;
    }
    return cartTotal + getSpFeeCents();
  }

  /**
   * 更新克隆结账按钮内部结构：
   * <span data-gwofy-checkout-mode="checkout-plus">Checkout •
   *   <span data-gwofy-checkout-origin="..." class="money">$16,256.77</span>
   * </span>
   * 保留原生 button 内的 svg 等非文案节点
   */
  function updateInlineCheckoutPrice(nativeBtn, cloneBtn) {
    var ts = resolveThemeSelectors();
    if (ts.checkoutMode !== "inline" || !cloneBtn || !state.cart) return;

    var sep = ts.checkoutPriceSeparator || " • ";
    var label = extractCheckoutLabel(nativeBtn, sep);
    var originCents = computeCheckoutTotalCents();
    var currency = getDisplayCurrency();
    var priceText = formatCheckoutDisplayAmount(originCents, currency);

    var cartOrigin = (state.cart && state.cart.total_price) || 0;
    var moneySpan = cloneBtn.querySelector("[" + ATTR_CHECKOUT_ORIGIN + "]");
    var modeSpan = cloneBtn.querySelector(
      '[' + ATTR_CHECKOUT_MODE + '="checkout-plus"]'
    );
    var originKey = String(cartOrigin);

    if (moneySpan && !modeSpan) {
      if (
        moneySpan.textContent === priceText &&
        moneySpan.getAttribute(ATTR_CHECKOUT_ORIGIN) === originKey
      ) {
        return;
      }
      moneySpan.setAttribute(ATTR_CHECKOUT_ORIGIN, originKey);
      moneySpan.className = moneySpan.className || "money";
      moneySpan.textContent = priceText;
      return;
    }

    if (modeSpan && moneySpan) {
      if (
        moneySpan.textContent === priceText &&
        moneySpan.getAttribute(ATTR_CHECKOUT_ORIGIN) === originKey
      ) {
        return;
      }
    }

    if (!modeSpan) {
      var i;
      var nodes = [];
      for (i = 0; i < cloneBtn.childNodes.length; i++) {
        nodes.push(cloneBtn.childNodes[i]);
      }
      nodes.forEach(function (node) {
        if (node.nodeType === 3) {
          cloneBtn.removeChild(node);
          return;
        }
        if (node.nodeType === 1) {
          var tag = node.tagName ? node.tagName.toUpperCase() : "";
          if (tag === "SVG") return;
          if (tag === "SPAN" && node.getAttribute(ATTR_CHECKOUT_MODE)) return;
          cloneBtn.removeChild(node);
        }
      });
      modeSpan = document.createElement("span");
      modeSpan.setAttribute(ATTR_CHECKOUT_MODE, "checkout-plus");
      cloneBtn.appendChild(modeSpan);
    }

    var parts = resolveCheckoutLineParts(ts, label, sep, priceText);

    while (modeSpan.firstChild) {
      modeSpan.removeChild(modeSpan.firstChild);
    }
    if (!parts.usePriceSpan) {
      modeSpan.appendChild(document.createTextNode(parts.prefix));
      return;
    }
    if (parts.prefix) {
      modeSpan.appendChild(document.createTextNode(parts.prefix));
    }
    moneySpan = document.createElement("span");
    moneySpan.className = "money";
    moneySpan.setAttribute(ATTR_CHECKOUT_ORIGIN, String(cartOrigin));
    moneySpan.textContent = priceText;
    modeSpan.appendChild(moneySpan);
    if (parts.suffix) {
      modeSpan.appendChild(document.createTextNode(parts.suffix));
    }
  }

  // ---------------------------------------------------------------------------
  // Shopify Cart API
  // ---------------------------------------------------------------------------

  /** 拼接 Cart API URL（含 _gwofy=1） */
  function cartUrl(path) {
    var root = shopifyRoot();
    var base = root + (root.slice(-1) === "/" ? "" : "/");
    return base + path + "?" + CART_QS + "&_=" + Date.now();
  }

  /** 构建同源 fetch init */
  function buildAjaxFetchInit(options) {
    options = options || {};
    var init = { credentials: AJAX_CREDENTIALS };
    if (options.method) init.method = options.method;
    if (options.headers) init.headers = options.headers;
    if (options.body != null) init.body = options.body;
    if (options.signal) init.signal = options.signal;
    return init;
  }

  /** fetch 并解析 JSON */
  function fetchJson(url, options) {
    var init = buildAjaxFetchInit(options);
    if (window.__gwofy_debug_mode__) {
      window.__gwofy_last_ajax_fetch_init__ = init;
      log("ajax fetch init", url, init.credentials);
    }
    return nativeFetch(url, init).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  /**
   * 完整 cart（cart.js / change.js / Liquid 快照）：有 items + item_count。
   * add.js 多行响应仅有 items、无 item_count，不算完整 cart。
   */
  function isFullCartJson(data) {
    return !!(
      data &&
      Array.isArray(data.items) &&
      typeof data.item_count === "number"
    );
  }

  /** 主题 section 响应等可能缺 item_count，从 items 推算 */
  function normalizeThemeCartResponse(data) {
    if (!data || !Array.isArray(data.items)) return data;
    if (typeof data.item_count === "number") return data;
    var count = 0;
    for (var i = 0; i < data.items.length; i++) {
      count += Number(data.items[i].quantity) || 0;
    }
    return Object.assign({}, data, { item_count: count });
  }

  /** GET cart.js 更新 state.cart */
  function getCart() {
    return fetchJson(cartUrl("cart.js")).then(function (cart) {
      state.cart = cart;
      return cart;
    });
  }

  /**
   * 将 add.js 返回的行（或 { items: [...] }）合并进当前 cart，避免再拉 cart.js
   */
  function mergeAddIntoCart(cart, data) {
    if (!cart || !Array.isArray(cart.items) || !data) return null;
    var lines = [];
    if (Array.isArray(data.items)) {
      lines = data.items;
    } else if (data.variant_id != null || data.id != null || data.key) {
      lines = [data];
    }
    if (!lines.length) return null;

    var items = cart.items.slice();
    var touched = false;
    for (var n = 0; n < lines.length; n++) {
      var line = lines[n];
      if (!line) continue;
      var key = line.key;
      var vid = line.variant_id != null ? line.variant_id : line.id;
      var idx = -1;
      for (var i = 0; i < items.length; i++) {
        if (key && items[i].key === key) {
          idx = i;
          break;
        }
        if (vid != null && String(items[i].variant_id) === String(vid)) {
          idx = i;
          break;
        }
      }
      if (idx >= 0) {
        items[idx] = Object.assign({}, items[idx], line);
      } else {
        items.push(line);
      }
      touched = true;
    }
    if (!touched) return null;

    var next = Object.assign({}, cart, { items: items });
    var count = 0;
    for (var j = 0; j < items.length; j++) {
      count += Number(items[j].quantity) || 0;
    }
    next.item_count = count;
    return next;
  }

  /** add/change 响应：完整 cart 直接用；add 片段则合并；否则 fallback cart.js */
  function applyCartMutationResult(data) {
    if (isFullCartJson(data)) {
      state.cart = data;
      return Promise.resolve(data);
    }
    var merged = mergeAddIntoCart(state.cart, data);
    if (merged && isFullCartJson(merged)) {
      state.cart = merged;
      return Promise.resolve(merged);
    }
    return getCart();
  }

  /** POST cart/add.js */
  function cartAdd(variantId, quantity) {
    return fetchJson(cartUrl("cart/add.js"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: [{ id: variantId, quantity: quantity || 1 }] }),
    }).then(applyCartMutationResult);
  }

  /** POST cart/change.js */
  function cartChange(lineKey, quantity) {
    return fetchJson(cartUrl("cart/change.js"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: lineKey, quantity: quantity }),
    }).then(applyCartMutationResult);
  }

  /** 将 SP 行 quantity 置 0 */
  function removeSpLine() {
    var line = findSpLine(state.cart);
    if (!line) return Promise.resolve(state.cart);
    return cartChange(line.key, 0);
  }

  /** SP 行已与当前算价 variant 一致，无需 cart/add|change */
  function isSpLineSyncedWithCompute() {
    if (!state.switchOn || !state.compute || state.compute.extId === "0") {
      return !findSpLine(state.cart);
    }
    var line = findSpLine(state.cart);
    if (!line) return false;
    return (
      String(line.variant_id) === String(state.compute.extId) &&
      Number(line.quantity) === 1
    );
  }

  /** 按 compute 添加/更新/移除 SP 行 */
  function syncSpLineToCompute() {
    if (!state.switchOn || !state.compute || state.compute.extId === "0") {
      return removeSpLine();
    }
    if (isSpLineSyncedWithCompute()) {
      return Promise.resolve(state.cart);
    }
    var extId = state.compute.extId;
    var line = findSpLine(state.cart);
    if (line) {
      return cartChange(line.key, 0).then(function () {
        return cartAdd(extId, 1);
      });
    }
    return cartAdd(extId, 1);
  }

  // ---------------------------------------------------------------------------
  // Cart 生命周期
  // 阶段 1：Liquid 注入 GWOFY_INITIAL_CART → init 同步挂载 SP
  // 阶段 2：hook 拦截主题 cart/add|change|update 响应 → 增量刷新（免 cart.js）
  // 阶段 3：无法解析或合并响应 → fallback getCart()
  // ---------------------------------------------------------------------------

  /** 最近一次由主题 hook 更新 cart 的时间戳 */
  var themeCartHandledAt = 0;
  /** hook 更新后抑制 cart.js 拉取的毫秒数 */
  /** hook 更新后抑制 cart.js 拉取的毫秒数 */
  var THEME_CART_SUPPRESS_MS = 500;

  /** 记录主题 hook 已更新 cart */
  function markThemeCartHandled() {
    themeCartHandledAt = Date.now();
  }

  /** 防抖：fallback 拉 cart.js 并刷新 UI */
  var debouncedOnCartChanged = debounce(onCartChanged, 120);

  /** 延迟 fallback getCart（若 hook 未处理） */
  function scheduleThemeCartRefresh() {
    if (Date.now() - themeCartHandledAt < THEME_CART_SUPPRESS_MS) {
      log("skip cart.js, hook already applied cart");
      return;
    }
    debouncedOnCartChanged();
  }

  /** 主题 cart 响应 → 完整 cart / 合并 add 行 / fallback cart.js */
  function onThemeCartMutationResponse(data) {
    data = normalizeThemeCartResponse(data);
    if (isFullCartJson(data)) {
      markThemeCartHandled();
      applyCartFromHook(data);
      return;
    }
    var merged = mergeAddIntoCart(state.cart, data);
    if (merged && isFullCartJson(merged)) {
      markThemeCartHandled();
      applyCartFromHook(merged);
      return;
    }
    scheduleThemeCartRefresh();
  }

  /** 响应 Content-Type 是否可能为 cart JSON */
  function responseMayBeCartJson(urlStr, res) {
    if (urlStr.indexOf(".js") >= 0) return true;
    if (!res.headers || !res.headers.get) return false;
    var ct = res.headers.get("content-type") || "";
    return ct.indexOf("application/json") >= 0 || ct.indexOf("text/javascript") >= 0;
  }

  /** hook fetch 响应并解析 cart */
  function parseHookedFetchResponse(res, urlStr) {
    if (!responseMayBeCartJson(urlStr, res)) {
      scheduleThemeCartRefresh();
      return Promise.resolve(res);
    }
    return res
      .clone()
      .json()
      .then(onThemeCartMutationResponse)
      .catch(function () {
        scheduleThemeCartRefresh();
      })
      .then(function () {
        return res;
      });
  }

  /** Liquid 首屏 cart 快照（HTML 生成时），供 init 同步渲染 */
  function bootstrapCartFromLiquid() {
    var raw = window.GWOFY_INITIAL_CART;
    if (!isFullCartJson(raw)) return false;
    state.cart = raw;
    log("bootstrap cart from liquid", raw.item_count, "items");
    return true;
  }

  /** 配置默认勾选开关 */
  function prepareDefaultSwitch() {
    if (!window.__gwofy_sp_disable_check__ && window.__gwofy_isCartDefaultOpen) {
      state.switchOn = true;
    }
  }

  /** 购物车含 SP 行时同步 switchOn */
  function syncSwitchFromCart() {
    if (findSpLine(state.cart)) {
      state.switchOn = true;
    }
  }

  /** syncStorefrontVisibility 入口 */
  function mountStorefrontUi() {
    syncStorefrontVisibility();
  }

  /** 移除所有 #gwofyWrapper */
  function unmountGwofyWidgets() {
    var nodes = document.querySelectorAll("#" + WRAPPER_ID);
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].parentNode) {
        nodes[i].parentNode.removeChild(nodes[i]);
      }
    }
    state.mounted = {};
    state.lastWidgetUi = null;
  }

  /** 不可售 SP 时恢复主题原生结账按钮，移除克隆与「无保障」链接 */
  function restoreNativeCheckoutUi() {
    queryCheckoutButtons().forEach(function (item) {
      var nativeBtn = item.el;
      var parent = nativeBtn.parentNode;
      if (!parent) return;
      nativeBtn.style.removeProperty("display");
      var clone = parent.querySelector("[" + ATTR_CHECKOUT + '="true"]');
      if (clone && clone.parentNode) {
        clone.parentNode.removeChild(clone);
      }
    });
    var withoutBtns = document.querySelectorAll(
      "[" + ATTR_CHECKOUT_WITHOUT + '="true"]'
    );
    for (var w = 0; w < withoutBtns.length; w++) {
      if (withoutBtns[w].parentNode) {
        withoutBtns[w].parentNode.removeChild(withoutBtns[w]);
      }
    }
  }

  /**
   * 按购物车与算价切换店面 UI：
   * - 无普通商品 → 隐藏 #gwofyWrapper
   * - 有商品且 SP 可售 → 挂件 + checkoutPlus
   * - 有商品但不可售 → 仅展示挂件（开关禁用），恢复原生结账
   */
  function syncStorefrontVisibility() {
    if (!cartHasMerchandise(state.cart)) {
      state.switchOn = false;
      unmountGwofyWidgets();
      restoreNativeCheckoutUi();
      purgeSpCartLineItemsFromDom();
      return;
    }

    if (needsWidgetMount()) {
      mountWidgets();
    } else {
      renderAllWidgets();
    }

    if (isSpOfferAvailable()) {
      mountCheckoutUi();
      refreshCheckoutUi();
    } else {
      state.switchOn = false;
      restoreNativeCheckoutUi();
    }
    purgeSpCartLineItemsFromDom();
  }

  /** 算价 → UI → SP 行同步 → 再算价 */
  function runCartUiPipeline() {
    applyComputeAndUi();
    return afterCartDataReady().then(function () {
      applyComputeAndUi();
    });
  }

  /** 带 busy 锁执行 cart 刷新任务 */
  function runCartRefreshWithBusy(work) {
    if (state.busy) return Promise.resolve();
    state.busy = true;
    return Promise.resolve()
      .then(work)
      .catch(function (e) {
        log("cart refresh error", e);
      })
      .finally(function () {
        state.busy = false;
      });
  }

  /** fallback：主动拉 cart.js 再跑 pipeline */
  function onCartChanged() {
    return runCartRefreshWithBusy(function () {
      return getCart().then(function () {
        return runCartUiPipeline();
      });
    });
  }

  /** hook 解析到完整 cart：更新 state 并刷新，跳过 cart.js */
  function applyCartFromHook(cart) {
    if (!isFullCartJson(cart)) {
      onCartChanged();
      return;
    }
    runCartRefreshWithBusy(function () {
      state.cart = cart;
      return runCartUiPipeline();
    });
  }

  /** 拦截 cart/add|change|update，line-items 变化后重算 */
  function installCartHook() {
    if (window.__gwofy_cart_hook__) return;
    window.__gwofy_cart_hook__ = true;

    var paths = ["/cart/add", "/cart/change", "/cart/update"];

    /** 是否为需拦截的主题 cart Ajax URL（排除本应用 _gwofy=1 请求） */
    function cartUrlMatched(url) {
      if (!url) return false;
      var s = String(url);
      /* 本应用 cart/add|change 带 _gwofy=1，已由 sync 链路更新 UI，避免重复 getCart */
      if (s.indexOf("_gwofy=1") >= 0) return false;
      return paths.some(function (p) {
        return s.indexOf(p) >= 0;
      });
    }

    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var matched = cartUrlMatched(url);
      return origFetch.apply(this, arguments).then(function (res) {
        if (!matched || !res.ok) return res;
        return parseHookedFetchResponse(res, String(url));
      });
    };

    /* 主题 drawer 常用 XHR：解析 responseText，与 fetch 同路径 */
    var XHR = window.XMLHttpRequest;
    if (XHR && !window.__gwofy_xhr_cart_hook__) {
      window.__gwofy_xhr_cart_hook__ = true;
      var origOpen = XHR.prototype.open;
      var origSend = XHR.prototype.send;
      XHR.prototype.open = function (method, url) {
        this.__gwofy_cart_url__ = url;
        return origOpen.apply(this, arguments);
      };
      XHR.prototype.send = function () {
        var xhr = this;
        if (cartUrlMatched(xhr.__gwofy_cart_url__)) {
          xhr.addEventListener("load", function () {
            if (xhr.status < 200 || xhr.status >= 300) return;
            var text = xhr.responseText;
            if (!text) {
              scheduleThemeCartRefresh();
              return;
            }
            try {
              onThemeCartMutationResponse(JSON.parse(text));
            } catch (e) {
              scheduleThemeCartRefresh();
            }
          });
        }
        return origSend.apply(this, arguments);
      };
    }

    // document.addEventListener("cart:updated", scheduleCartRefresh);
    // document.addEventListener("cart:refresh", scheduleCartRefresh);
  }

  // ---------------------------------------------------------------------------
  // SP 商品 /products/{handle}.js
  // ---------------------------------------------------------------------------

  /** 归一化 /products/*.js 变体价格为分 */
  function normalizeProduct(json) {
    var variants = (json && json.variants) || [];
    return {
      id: json.id,
      handle: json.handle,
      published_at: json.published_at,
      variants: variants.map(function (v) {
        /* Shopify /products/*.js：price 一般为分（整数），字符串时为元 */
        var price = v.price;
        if (typeof price === "string") {
          price = Math.round(parseFloat(price) * 100);
        } else {
          price = Math.round(Number(price) || 0);
        }
        return {
          id: v.id,
          price: price,
          sku: v.sku || "",
          title: v.public_title || v.title || "",
        };
      }),
    };
  }

  /** 加载 SP 商品 JSON */
  function loadProduct() {
    var handle = config.productHandle || "gwofy-shipping-protection-qaqwer";
    var url = cartUrl("products/" + encodeURIComponent(handle) + ".js");
    return fetchJson(url).then(function (json) {
      state.product = normalizeProduct(json);
      if (!state.product.variants.length) {
        throw new Error("SP product has no variants: " + handle);
      }
      log("product loaded", state.product.variants.length, "variants");
      return state.product;
    });
  }

  // ---------------------------------------------------------------------------
  // UI：样式与挂件
  // ---------------------------------------------------------------------------

  /** 注入挂件 CSS */
  function injectStyles() {
    refreshConfig();
    var sheet = resolveStyleConfig();
    injectStyleSheet(
      STYLE_ID,
      sheet.widget + (sheet.widgetExtra || "")
    );
  }

  /**
   * #gwofyWrapper 初始 DOM（模板字符串）。
   * text.sp → [data-gwofy-desc]；开关 [data-gwofy-switch]；说明图标 [data-gwofy-tips]。
   */
  function buildWrapperHtml(priceText, checked, disabled) {
    var sp = calcTextSp();
    var imgs = widgetImageSrc();
    var dis = disabled ? " disabled" : "";
    var chk = checked ? " checked" : "";
    var priceDisplay = priceText ? "block" : "none";

    return `
<div id="${WRAPPER_ID}" class="${widgetWrapperClasses()}">
  <div class="gwofy_cnt_wrapper">
    <div class="gwofy_cnt">
      <div class="gwofy_bd">
        <div class="gwofy_bd_title">
          <div class="gwofy_bd_title_txt" data-gwofy-switch-area role="button" tabindex="0"><img class="gwofy_bd_title_img" src="${imgs.titleIcon}" alt="" /><p><span style="font-size: 15px;">${escapeHtml(String(sp.title || "Shipping Protection").trim())}</span></p></div>
          <img class="gwofy_tips" id="gwofy-tip" data-gwofy-tips src="${imgs.tipsIcon}" alt="" />
        </div>
        <div class="gwofy_price" data-gwofy-price style="display:${priceDisplay}!important;">${priceText}</div>
      </div>
      <div class="gwofy_ft">
        <div class="gwofy_bd_desc" data-gwofy-desc><p><span style="font-size: 14px;">${escapeHtml(String(sp.desc || "").trim())}</span></p></div>
      </div>
    </div>
  </div>
  <input type="checkbox" class="gwofy_visually_hidden" data-gwofy-switch aria-label="Shipping protection"${chk}${dis} />
</div>`;
  }

  /** 绑定开关、标题区、说明图标事件 */
  function bindWidgetEvents(root) {
    if (!root || root.getAttribute("data-gwofy-bound")) return;
    root.setAttribute("data-gwofy-bound", "1");

    var sw = root.querySelector("[data-gwofy-switch]");
    if (sw) {
      sw.addEventListener("change", onSwitchChange);
    }

    var titleArea = root.querySelector("[data-gwofy-switch-area]");
    if (titleArea && sw) {
      /** 点击标题区域切换 checkbox */
      function toggleFromTitle(ev) {
        if (ev.type === "keydown" && ev.key !== "Enter" && ev.key !== " ") {
          return;
        }
        if (state.busy || sw.disabled) return;
        ev.preventDefault();
        sw.checked = !sw.checked;
        sw.dispatchEvent(new Event("change", { bubbles: true }));
      }
      titleArea.addEventListener("click", toggleFromTitle);
      titleArea.addEventListener("keydown", toggleFromTitle);
    }

    var tips = root.querySelector("[data-gwofy-tips]");
    if (tips) {
      tips.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        openTipsDialog();
      });
    }
  }

  /** 挂件锚点去重 key */
  function mountKey(anchor) {
    return (anchor.selector || "") + "|" + (anchor.position || "before");
  }

  /** 解析锚点选择器为 DOM 列表 */
  function queryAnchors(list) {
    var out = [];
    (list || []).forEach(function (anchor) {
      if (!anchor || !anchor.selector) return;
      var nodes = document.querySelectorAll(anchor.selector);
      for (var i = 0; i < nodes.length; i++) {
        out.push({
          el: nodes[i],
          position: anchor.position || "before",
        });
      }
    });
    return out;
  }

  /** 在锚点 before/after 插入 HTML */
  function insertRelative(el, position, html) {
    var wrap = document.createElement("div");
    wrap.innerHTML = html.trim();
    var node = wrap.firstElementChild;
    if (!node) return;
    if (position === "after") {
      el.parentNode.insertBefore(node, el.nextSibling);
    } else {
      el.parentNode.insertBefore(node, el);
    }
    return node;
  }

  /** 更新已挂载挂件的价格、开关与描述（描述来自 text.sp.desc，不用 limit.tips） */
  function renderAllWidgets() {
    var priceText = "—";
    if (state.compute && Number(state.compute.totalPriceInt) > 0) {
      priceText = formatShopMoney(
        state.compute.totalPriceInt,
        state.compute.currency || getDisplayCurrency()
      );
    } else if (state.compute && state.compute.extId === "0") {
      priceText = "";
    }

    var display = priceText ? "block" : "none";
    var descHtml = `<p><span style="font-size: 14px;">${escapeHtml(String(calcTextSp().desc || "").trim())}</span></p>`;
    var ui = state.lastWidgetUi;
    if (
      ui &&
      ui.priceText === priceText &&
      ui.display === display &&
      ui.descHtml === descHtml &&
      ui.switchOn === state.switchOn &&
      ui.conditionOk === state.conditionOk &&
      ui.busy === state.busy
    ) {
      return;
    }
    state.lastWidgetUi = {
      priceText: priceText,
      display: display,
      descHtml: descHtml,
      switchOn: state.switchOn,
      conditionOk: state.conditionOk,
      busy: state.busy,
    };

    var priceEls = document.querySelectorAll(
      "#" + WRAPPER_ID + " [data-gwofy-price]"
    );
    for (var p = 0; p < priceEls.length; p++) {
      var el = priceEls[p];
      if (el.textContent !== priceText) {
        el.textContent = priceText;
      }
      el.style.setProperty("display", display, "important");
    }

    var nodes = document.querySelectorAll("#" + WRAPPER_ID);
    for (var i = 0; i < nodes.length; i++) {
      var root = nodes[i];
      var descEl = root.querySelector("[data-gwofy-desc]");
      if (descEl && descEl.innerHTML !== descHtml) {
        descEl.innerHTML = descHtml;
      }
      var sw = root.querySelector("[data-gwofy-switch]");
      if (sw) {
        if (sw.checked !== state.switchOn) {
          sw.checked = state.switchOn;
        }
        var disabled = !state.conditionOk || state.busy;
        if (sw.disabled !== disabled) {
          sw.disabled = disabled;
        }
      }
    }
  }

  /** 是否存在未挂载的锚点 */
  function needsWidgetMount() {
    var anchors = queryAnchors(resolveThemeSelectors().widgetAnchors);
    for (var i = 0; i < anchors.length; i++) {
      var item = anchors[i];
      var prev =
        item.position === "after"
          ? item.el.nextElementSibling
          : item.el.previousElementSibling;
      if (!prev || prev.id !== WRAPPER_ID) {
        return true;
      }
    }
    return false;
  }

  /** 在锚点插入挂件 DOM */
  function mountWidgets() {
    var didMount = false;
    var anchors = queryAnchors(resolveThemeSelectors().widgetAnchors);
    anchors.forEach(function (item) {
      var key = mountKey({ selector: item.el.tagName, position: item.position });
      var prev =
        item.position === "after"
          ? item.el.nextElementSibling
          : item.el.previousElementSibling;
      if (prev && prev.id === WRAPPER_ID) return;

      var html = buildWrapperHtml(
        "—",
        state.switchOn,
        !state.conditionOk
      );
      var node = insertRelative(item.el, item.position, html);
      if (node) {
        bindWidgetEvents(node);
        state.mounted[key] = true;
        didMount = true;
        state.lastWidgetUi = null;
      }
    });
    if (didMount) {
      renderAllWidgets();
    }
  }

  // ---------------------------------------------------------------------------
  // checkoutPlus：克隆结账按钮 + 无保障链接
  // ---------------------------------------------------------------------------

  /** 查询主题结账按钮节点 */
  function queryCheckoutButtons() {
    var list = resolveThemeSelectors().checkoutBtn;
    var out = [];
    list.forEach(function (item) {
      if (!item.query) return;
      var nodes = document.querySelectorAll(item.query);
      for (var i = 0; i < nodes.length; i++) {
        out.push({ el: nodes[i], cfg: item });
      }
    });
    return out;
  }

  /** 克隆并隐藏原生结账按钮 */
  function insertCheckoutClone(nativeBtn, itemCfg) {
    var display = itemCfg.display || "inline-block";
    var parent = nativeBtn.parentNode;
    if (!parent) return;

    var existing = parent.querySelector("[" + ATTR_CHECKOUT + '="true"]');
    if (existing) {
      existing.style.setProperty("display", display, "important");
      updateInlineCheckoutPrice(nativeBtn, existing);
      return;
    }

    nativeBtn.style.setProperty("display", "none", "important");
    var clone = nativeBtn.cloneNode(true);
    clone.setAttribute(ATTR_CHECKOUT, "true");
    clone.removeAttribute("id");
    clone.removeAttribute("name");
    (itemCfg.noCheckoutProps || []).forEach(function (prop) {
      clone.removeAttribute(prop);
    });
    clone.style.setProperty("display", display, "important");

    parent.insertBefore(clone, nativeBtn);

    clone.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      onCheckoutWithProtection(nativeBtn);
    });

    updateInlineCheckoutPrice(nativeBtn, clone);
  }

  /** 在容器附近找原生 checkout 按钮 */
  function findNativeCheckoutNear(container) {
    if (!container) return null;
    var btn = container.querySelector('button[name="checkout"]');
    if (btn) return btn;
    var list = queryCheckoutButtons();
    return list.length ? list[0].el : null;
  }

  /** 挂载无保障结账链接 */
  function mountCheckoutWithout() {
    var list = resolveThemeSelectors().checkoutWithout;
    var text = widgetCopy().checkoutWithout;

    list.forEach(function (anchor) {
      if (!anchor.selector) return;
      var nodes = document.querySelectorAll(anchor.selector);
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        var sibling =
          anchor.position === "after" ? el.nextElementSibling : el.previousElementSibling;
        if (
          sibling &&
          sibling.getAttribute &&
          sibling.getAttribute(ATTR_CHECKOUT_WITHOUT) === "true"
        ) {
          continue;
        }
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "gwofy-checkout-without";
        btn.setAttribute(ATTR_CHECKOUT_WITHOUT, "true");
        btn.textContent = text;
        btn.addEventListener("click", function (ev) {
          ev.preventDefault();
          var nativeBtn = findNativeCheckoutNear(el);
          if (nativeBtn) onCheckoutWithoutProtection(nativeBtn);
        });
        if (anchor.position === "after") {
          el.parentNode.insertBefore(btn, el.nextSibling);
        } else {
          el.parentNode.insertBefore(btn, el);
        }
      }
    });
  }

  /** 挂载克隆结账 + 无保障链接 */
  function mountCheckoutUi() {
    queryCheckoutButtons().forEach(function (item) {
      insertCheckoutClone(item.el, item.cfg);
    });
    mountCheckoutWithout();
  }

  /** 刷新克隆按钮内价格 */
  function refreshCheckoutUi() {
    queryCheckoutButtons().forEach(function (item) {
      var parent = item.el.parentNode;
      if (!parent) return;
      var clone = parent.querySelector("[" + ATTR_CHECKOUT + '="true"]');
      if (clone) updateInlineCheckoutPrice(item.el, clone);
    });
  }

  // ---------------------------------------------------------------------------
  // 业务事件
  // ---------------------------------------------------------------------------

  /** 算价 + 限额；showBoard 控制显隐，不根据 limit.tips 改 text.sp.descHtml */
  function applyComputeAndUi() {
    refreshConfig();
    var limit = checkLimitSp(state.cart);
    state.spLimit = limit;
    state.conditionOk = limit.ok;
    state.compute = runCalculate();

    if (!limit.showBoard) {
      state.switchOn = false;
    } else if (
      isSpOfferAvailable() &&
      !window.__gwofy_sp_disable_check__ &&
      window.__gwofy_isCartDefaultOpen &&
      !window.__gwofy_switch_touched__
    ) {
      state.switchOn = true;
    }

    syncStorefrontVisibility();
    log("compute", state.compute, "calcTrace", window.__gwofy_last_calc__ && window.__gwofy_last_calc__.calcTrace, "limit", limit, "spUi", isSpOfferAvailable());
  }

  /** 保障开关 change 事件 */
  function onSwitchChange(ev) {
    var input = ev.target;
    window.__gwofy_switch_touched__ = true;
    state.switchOn = !!input.checked;
    state.busy = true;
    applyComputeAndUi();

    var chain;
    if (state.switchOn) {
      chain = isCartPage() ? stripSpLinesOnCartPage() : syncSpLineToCompute();
    } else {
      chain = removeSpLine();
      if (isCartPage()) {
        chain = chain.then(function (cart) {
          state.cart = cart || state.cart;
          return refreshCartLineSections().then(function () {
            remountGwofyCartUi();
            return cart;
          });
        });
      }
    }

    chain
      .then(function () {
        applyComputeAndUi();
      })
      .catch(function (e) {
        log("switch sync error", e);
        input.checked = false;
        state.switchOn = false;
      })
      .finally(function () {
        state.busy = false;
      });
  }

  /** 克隆结账：先确保 SP 行再触发原生结账 */
  function onCheckoutWithProtection(nativeBtn) {
    if (state.busy || !isSpOfferAvailable()) return;
    state.busy = true;
    state.switchOn = true;
    applyComputeAndUi();

    syncSpLineToCompute()
      .then(function () {
        nativeBtn.click();
      })
      .catch(function (e) {
        log("checkout SP error", e);
      })
      .finally(function () {
        state.busy = false;
      });
  }

  /** 无保障结账：移除 SP 后点原生按钮 */
  function onCheckoutWithoutProtection(nativeBtn) {
    if (state.busy) return;
    state.busy = true;
    state.switchOn = false;
    renderAllWidgets();

    removeSpLine()
      .then(function () {
        nativeBtn.click();
      })
      .finally(function () {
        state.busy = false;
      });
  }

  // ---------------------------------------------------------------------------
  // DOM 变更：主题重绘 cart 后重新挂载（仅监听购物车相关区域）
  // ---------------------------------------------------------------------------

  /** 节点是否在购物车相关区域 */
  function isCartRelatedNode(node) {
    if (!node || node.nodeType !== 1 || !node.closest) return false;
    return !!(
      node.closest("#cart-drawer") ||
      node.closest("cart-drawer") ||
      node.closest(".cart-drawer__checkout-buttons") ||
      node.closest(".cart__ctas") ||
      node.closest("main.cart") ||
      node.closest("form.cart") ||
      node.closest(".cart")
    );
  }

  /** 节点是否在挂件内 */
  function isInsideGwofyWrapper(node) {
    if (!node || node.nodeType !== 1) return false;
    return !!(node.closest && node.closest("#" + WRAPPER_ID));
  }

  /** mutation 是否仅涉及 Gwofy 节点 */
  function mutationOnlyGwofyNodes(mutation) {
    var nodes = [];
    var a;
    for (a = 0; a < mutation.addedNodes.length; a++) {
      if (mutation.addedNodes[a].nodeType === 1) {
        nodes.push(mutation.addedNodes[a]);
      }
    }
    for (a = 0; a < mutation.removedNodes.length; a++) {
      if (mutation.removedNodes[a].nodeType === 1) {
        nodes.push(mutation.removedNodes[a]);
      }
    }
    if (!nodes.length) return false;
    for (a = 0; a < nodes.length; a++) {
      var n = nodes[a];
      if (n.id === WRAPPER_ID) continue;
      if (n.closest && n.closest("#" + WRAPPER_ID)) continue;
      if (n.querySelector && n.querySelector("#" + WRAPPER_ID)) continue;
      return false;
    }
    return true;
  }

  /** mutation 是否移除了挂件 */
  function gwofyWrapperRemoved(mutation) {
    for (var i = 0; i < mutation.removedNodes.length; i++) {
      var n = mutation.removedNodes[i];
      if (n.nodeType !== 1) continue;
      if (n.id === WRAPPER_ID) return true;
      if (n.querySelector && n.querySelector("#" + WRAPPER_ID)) return true;
    }
    return false;
  }

  /** 是否在 /cart 行列表区域 */
  function isCartPageLineListNode(node) {
    if (!node || node.nodeType !== 1 || !isCartPage()) return false;
    return !!(
      node.closest("#cart .cart__items") ||
      node.closest("main.cart .cart__items") ||
      node.closest("form.cart .cart__items") ||
      node.closest("#cart cart-items") ||
      node.closest("cart-items")
    );
  }

  /** 是否应对 cart 行列表 mutation（未用） */
  function shouldReactToCartLineMutations(mutations) {
    if (isCartUiLocked()) return false;
    if (!isCartPage()) return false;
    for (var i = 0; i < mutations.length; i++) {
      var target = mutations[i].target;
      if (isCartPageLineListNode(target)) return true;
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        if (added[j].nodeType === 1 && isCartPageLineListNode(added[j])) {
          return true;
        }
      }
    }
    return false;
  }

  /** Observer 是否应触发 remount */
  function shouldReactToMutations(mutations) {
    if (isCartUiLocked()) return false;
    for (var i = 0; i < mutations.length; i++) {
      var m = mutations[i];
      var target = m.target;
      if (!isCartRelatedNode(target)) continue;
      if (isCartPageLineListNode(target)) continue;
      if (gwofyWrapperRemoved(m)) return true;
      if (isInsideGwofyWrapper(target) && !gwofyWrapperRemoved(m)) continue;
      if (mutationOnlyGwofyNodes(m)) continue;
      return true;
    }
    return false;
  }

  /** 收集 MutationObserver 根节点 */
  function collectObserverRoots() {
    var roots = [];
    var seen = [];

    function push(el) {
      if (!el || el.nodeType !== 1) return;
      if (seen.indexOf(el) >= 0) return;
      seen.push(el);
      roots.push(el);
    }

    var selectors = [
      "#cart-drawer",
      "cart-drawer",
      ".cart__ctas",
      ".cart-drawer__checkout-buttons",
      "main.cart",
      "form.cart",
    ];
    var ts = resolveThemeSelectors();
    ts.widgetAnchors.forEach(function (a) {
      if (a.selector) selectors.push(a.selector);
    });

    selectors.forEach(function (sel) {
      try {
        document.querySelectorAll(sel).forEach(function (node) {
          push(node.closest("#cart-drawer") || node.closest("cart-drawer"));
          push(
            node.closest("main.cart") ||
              node.closest("form.cart") ||
              node.closest(".cart")
          );
          if (node.id === "cart-drawer" || node.tagName === "CART-DRAWER") {
            push(node);
          } else if (
            node.classList &&
            (node.classList.contains("cart__ctas") ||
              node.classList.contains("cart-drawer__checkout-buttons"))
          ) {
            push(node);
          }
        });
      } catch (e) {
        /* 无效 selector 忽略 */
      }
    });

    /* /cart 不观察 cart-items，避免 section 刷新后 Observer 死循环 */

    return roots;
  }

  /** 防抖：主题重绘后重新挂载 cart UI */
  var remountCheckout = debounce(function () {
    remountGwofyCartUi();
  }, 800);

  /** 监听 cart 区域 DOM 变更 */
  function installDomObserver() {
    if (!window.MutationObserver) return;
    var obs = new MutationObserver(function (mutations) {
      if (isCartUiLocked()) return;
      if (!shouldReactToMutations(mutations)) return;
      remountCheckout();
    });

    /** 将 MutationObserver 挂到 cart 相关根节点 */
    function attach() {
      var roots = collectObserverRoots();
      if (!roots.length) {
        obs.observe(document.body, { childList: true, subtree: true });
        return;
      }
      roots.forEach(function (root) {
        try {
          obs.observe(root, { childList: true, subtree: true });
        } catch (e) {
          log("observer attach failed", e);
        }
      });
    }

    /** 主题 cart 结构变化后重新绑定 Observer */
    var reattachObserver = debounce(function () {
      obs.disconnect();
      attach();
    }, 500);

    attach();
    /* 侧栏 cart 晚于脚本注入时再挂一次观察点 */
    debounce(reattachObserver, 1500)();
    document.addEventListener("cart:updated", reattachObserver);
    document.addEventListener("cart:refresh", reattachObserver);
  }

  // ---------------------------------------------------------------------------
  // 初始化
  // ---------------------------------------------------------------------------

  /** 入口：hook、样式、product、cart、UI */
  function init() {
    if (!g.GWOFY_CONFIG_READY) {
      console.warn("[Gwofy Guard] init skipped: GWOFY_CONFIG not ready");
      return Promise.resolve();
    }

    refreshConfig();

    if (!supportsStorefront()) {
      log("SP not authorized or locale/currency not supported, skip");
      return Promise.resolve();
    }

    injectStyles();
    injectTipsDialogStyles();
    installCartHook();
    installDomObserver();

    document.addEventListener("gwofy:currency-changed", function () {
      if (state.busy) return;
      getCart()
        .then(function () {
          applyComputeAndUi();
        })
        .catch(function (e) {
          log("currency change refresh error", e);
        });
    });

    prepareDefaultSwitch();

    /* 阶段 1：Liquid cart 快照（UI 等 product/算价就绪后再挂载） */
    var hasLiquidCart = bootstrapCartFromLiquid();
    if (hasLiquidCart) {
      syncSwitchFromCart();
    }

    /* 阶段 2：补齐 SP 商品；无 Liquid 快照时才阻塞拉 cart.js */
    var cartPromise = hasLiquidCart ? Promise.resolve(state.cart) : getCart();

    return Promise.all([loadProduct(), cartPromise])
      .then(function () {
        if (!hasLiquidCart) {
          syncSwitchFromCart();
          mountStorefrontUi();
        }
        return runCartUiPipeline();
      })
      .then(function () {
        /* 阶段 3：Liquid 快照可能与服务端不一致，后台静默校正 */
        if (!hasLiquidCart) return;
        return getCart()
          .then(function () {
            return runCartUiPipeline();
          })
          .catch(function (e) {
            log("cart reconcile error", e);
          });
      })
      .catch(function (e) {
        console.error("[Gwofy Guard] init failed", e);
      });
  }

  g.GwofyStorefront = {
    init: init,
    remount: remountGwofyCartUi,
    refreshConfig: refreshConfig,
  };
})(typeof window !== "undefined" ? window : globalThis);
