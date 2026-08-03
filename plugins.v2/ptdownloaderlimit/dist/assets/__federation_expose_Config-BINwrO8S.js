import { importShared } from './__federation_fn_import-JrT3xvdd.js';

const _export_sfc = (sfc, props) => {
  const target = sfc.__vccOpts || sfc;
  for (const [key, val] of props) {
    target[key] = val;
  }
  return target;
};

const {createElementVNode:_createElementVNode,resolveComponent:_resolveComponent,createVNode:_createVNode,withCtx:_withCtx,createTextVNode:_createTextVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,renderList:_renderList,Fragment:_Fragment,createElementBlock:_createElementBlock,toDisplayString:_toDisplayString,withModifiers:_withModifiers} = await importShared('vue');


const _hoisted_1 = { class: "pt-limit-config" };
const _hoisted_2 = { class: "d-flex align-center mt-4 mb-3" };
const _hoisted_3 = { class: "d-flex align-center w-100 pe-2 rule-title" };
const _hoisted_4 = { class: "font-weight-medium text-truncate" };

const {computed,nextTick,onMounted,ref} = await importShared('vue');



const _sfc_main = {
  __name: 'Config',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'PTDownloaderLimit' },
  initialConfig: { type: Object, default: () => ({}) },
},
  emits: ['save', 'close', 'layout'],
  setup(__props, { emit: __emit }) {

const props = __props;

const emit = __emit;

const config = ref(defaultConfig());
const downloaderOptions = ref([]);
const siteOptions = ref([]);
const expanded = ref();
const loadingOptions = ref(false);
const snackbar = ref({ show: false, text: '', color: 'info' });

function newId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().replaceAll('-', '')
  return `${Date.now()}${Math.random().toString(16).slice(2)}`
}

function emptyRule() {
  return {
    id: newId(),
    mark: '',
    downloaders: [],
    limit_sites: [],
    limit_speed: 0,
    limit_sites_pause_threshold: 0,
    active_time_range_site_config: '',
  }
}

function defaultConfig() {
  return {
    enabled: false,
    onlyonce: false,
    notify: false,
    cron: '',
    nolabels: '',
    rules: [emptyRule()],
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value ?? {}))
}

function normalizeConfig(value) {
  const result = { ...defaultConfig(), ...clone(value) };
  result.rules = Array.isArray(value?.rules)
    ? value.rules.filter(rule => rule && typeof rule === 'object').map(rule => ({
        ...emptyRule(),
        ...rule,
        id: String(rule.id || newId()),
        mark: String(rule.mark || ''),
        downloaders: Array.isArray(rule.downloaders) ? rule.downloaders : [],
        limit_sites: Array.isArray(rule.limit_sites) ? rule.limit_sites : [],
        limit_speed: Number(rule.limit_speed || 0),
        limit_sites_pause_threshold: Number(rule.limit_sites_pause_threshold || 0),
        active_time_range_site_config: String(rule.active_time_range_site_config || ''),
      }))
    : [emptyRule()];
  return result
}

const pluginBase = computed(() => `plugin/${props.pluginId || 'PTDownloaderLimit'}`);

function showMessage(text, color = 'info') {
  snackbar.value = { show: true, text, color };
}

function unwrapResponse(response) {
  if (response?.data?.success !== undefined) return response.data
  if (response?.success !== undefined) return response
  return response?.data || response || {}
}

async function loadOptions() {
  if (!props.api?.get) return
  loadingOptions.value = true;
  try {
    const response = unwrapResponse(await props.api.get(`${pluginBase.value}/options`));
    if (!response?.success) throw new Error(response?.message || '加载选项失败')
    downloaderOptions.value = response.data?.downloaders || [];
    siteOptions.value = response.data?.sites || [];
  } catch (error) {
    showMessage(error?.message || '下载器和站点选项加载失败', 'error');
  } finally {
    loadingOptions.value = false;
  }
}

function addRule() {
  config.value.rules.push(emptyRule());
  nextTick(() => {
    expanded.value = config.value.rules.length - 1;
  });
}

function deleteRule(index) {
  config.value.rules.splice(index, 1);
  if (!config.value.rules.length) {
    expanded.value = undefined;
  } else if (expanded.value >= config.value.rules.length) {
    expanded.value = config.value.rules.length - 1;
  }
}

function moveRule(index, offset) {
  const target = index + offset;
  if (target < 0 || target >= config.value.rules.length) return
  const [rule] = config.value.rules.splice(index, 1);
  config.value.rules.splice(target, 0, rule);
  expanded.value = target;
}

function toggleRule(index) {
  expanded.value = expanded.value === index ? undefined : index;
}

function chineseNumber(value) {
  const digits = '零一二三四五六七八九';
  if (value < 10) return digits[value]
  if (value < 20) return `十${value % 10 ? digits[value % 10] : ''}`
  if (value < 100) return `${digits[Math.floor(value / 10)]}十${value % 10 ? digits[value % 10] : ''}`
  return String(value)
}

function ruleTitle(index, rule) {
  const title = `限速${chineseNumber(index + 1)}`;
  return rule.mark?.trim() ? `${title}：${rule.mark.trim()}` : title
}

function validate() {
  const timePattern = /^\d{2}:\d{2}-\d{2}:\d{2}$/;
  const validClock = value => {
    const [hour, minute] = value.split(':').map(Number);
    return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59
  };
  for (let index = 0; index < config.value.rules.length; index += 1) {
    const rule = config.value.rules[index];
    const range = rule.active_time_range_site_config?.trim();
    const clocks = range?.split('-') || [];
    if (range && (!timePattern.test(range) || !clocks.every(validClock))) {
      expanded.value = index;
      showMessage(`限速${chineseNumber(index + 1)}的时间段格式应为 HH:MM-HH:MM`, 'error');
      return false
    }
    if (Number(rule.limit_speed) < 0 || Number(rule.limit_sites_pause_threshold) < 0) {
      expanded.value = index;
      showMessage(`限速${chineseNumber(index + 1)}的速度和暂停分钟不能小于 0`, 'error');
      return false
    }
  }
  return true
}

function saveConfig() {
  if (!validate()) return
  const payload = clone(config.value);
  payload.rules = payload.rules.map(rule => ({
    ...rule,
    limit_speed: Math.max(0, Number(rule.limit_speed || 0)),
    limit_sites_pause_threshold: Math.max(0, Number(rule.limit_sites_pause_threshold || 0)),
    active_time_range_site_config: String(rule.active_time_range_site_config || '').trim(),
  }));
  emit('save', payload);
  showMessage('配置已提交保存', 'success');
}

onMounted(() => {
  emit('layout', { maxWidth: '90rem' });
  config.value = normalizeConfig(props.initialConfig);
  expanded.value = undefined;
  loadOptions();
});

return (_ctx, _cache) => {
  const _component_VSpacer = _resolveComponent("VSpacer");
  const _component_VBtn = _resolveComponent("VBtn");
  const _component_VToolbar = _resolveComponent("VToolbar");
  const _component_VDivider = _resolveComponent("VDivider");
  const _component_VSwitch = _resolveComponent("VSwitch");
  const _component_VCol = _resolveComponent("VCol");
  const _component_VCronField = _resolveComponent("VCronField");
  const _component_VTextField = _resolveComponent("VTextField");
  const _component_VRow = _resolveComponent("VRow");
  const _component_VAlert = _resolveComponent("VAlert");
  const _component_VExpansionPanelTitle = _resolveComponent("VExpansionPanelTitle");
  const _component_VSelect = _resolveComponent("VSelect");
  const _component_VExpansionPanelText = _resolveComponent("VExpansionPanelText");
  const _component_VExpansionPanel = _resolveComponent("VExpansionPanel");
  const _component_VExpansionPanels = _resolveComponent("VExpansionPanels");
  const _component_VForm = _resolveComponent("VForm");
  const _component_VSnackbar = _resolveComponent("VSnackbar");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createVNode(_component_VToolbar, {
      density: "comfortable",
      color: "transparent"
    }, {
      default: _withCtx(() => [
        _cache[8] || (_cache[8] = _createElementVNode("div", { class: "text-h6 ms-3" }, "QB&TR上传限速 - 插件配置", -1)),
        _createVNode(_component_VSpacer),
        _createVNode(_component_VBtn, {
          icon: "mdi-content-save",
          variant: "text",
          color: "primary",
          onClick: saveConfig
        }),
        _createVNode(_component_VBtn, {
          icon: "mdi-close",
          variant: "text",
          onClick: _cache[0] || (_cache[0] = $event => (emit('close')))
        })
      ]),
      _: 1
    }),
    _createVNode(_component_VDivider),
    _createVNode(_component_VForm, {
      class: "pa-4",
      onSubmit: _withModifiers(saveConfig, ["prevent"])
    }, {
      default: _withCtx(() => [
        _createVNode(_component_VRow, null, {
          default: _withCtx(() => [
            _createVNode(_component_VCol, {
              cols: "12",
              md: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: config.value.enabled,
                  "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((config.value.enabled) = $event)),
                  label: "启用插件",
                  color: "primary",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VCronField, {
                  modelValue: config.value.cron,
                  "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.value.cron) = $event)),
                  label: "执行周期"
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "3"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextField, {
                  modelValue: config.value.nolabels,
                  "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.value.nolabels) = $event)),
                  label: "不限速标签",
                  placeholder: "多个标签使用英文逗号分隔",
                  clearable: ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "6",
              md: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: config.value.notify,
                  "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.value.notify) = $event)),
                  label: "开启通知",
                  color: "primary",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "6",
              md: "3"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: config.value.onlyonce,
                  "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((config.value.onlyonce) = $event)),
                  label: "立即运行一次",
                  color: "primary",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            })
          ]),
          _: 1
        }),
        _createElementVNode("div", _hoisted_2, [
          _cache[10] || (_cache[10] = _createElementVNode("div", { class: "text-h6" }, "限速规则", -1)),
          _createVNode(_component_VSpacer),
          _createVNode(_component_VBtn, {
            color: "primary",
            "prepend-icon": "mdi-plus",
            variant: "tonal",
            onClick: addRule
          }, {
            default: _withCtx(() => [...(_cache[9] || (_cache[9] = [
              _createTextVNode(" 新增规则 ", -1)
            ]))]),
            _: 1
          })
        ]),
        (!config.value.rules.length)
          ? (_openBlock(), _createBlock(_component_VAlert, {
              key: 0,
              type: "info",
              variant: "tonal",
              class: "mb-4"
            }, {
              default: _withCtx(() => [...(_cache[11] || (_cache[11] = [
                _createTextVNode(" 当前没有限速规则，可点击“新增规则”开始配置。 ", -1)
              ]))]),
              _: 1
            }))
          : _createCommentVNode("", true),
        _createVNode(_component_VExpansionPanels, {
          modelValue: expanded.value,
          "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((expanded).value = $event)),
          variant: "accordion",
          class: "rule-panels"
        }, {
          default: _withCtx(() => [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(config.value.rules, (rule, index) => {
              return (_openBlock(), _createBlock(_component_VExpansionPanel, {
                key: rule.id,
                value: index,
                readonly: ""
              }, {
                default: _withCtx(() => [
                  _createVNode(_component_VExpansionPanelTitle, { "hide-actions": "" }, {
                    default: _withCtx(() => [
                      _createElementVNode("div", _hoisted_3, [
                        _createElementVNode("span", _hoisted_4, _toDisplayString(ruleTitle(index, rule)), 1),
                        _createVNode(_component_VSpacer),
                        _createVNode(_component_VBtn, {
                          icon: expanded.value === index ? 'mdi-chevron-down' : 'mdi-chevron-right',
                          size: "small",
                          variant: "text",
                          title: expanded.value === index ? '折叠' : '展开',
                          onClick: _withModifiers($event => (toggleRule(index)), ["stop"])
                        }, null, 8, ["icon", "title", "onClick"]),
                        _createVNode(_component_VBtn, {
                          icon: "mdi-arrow-up",
                          size: "small",
                          variant: "text",
                          disabled: index === 0,
                          title: "上移",
                          onClick: _withModifiers($event => (moveRule(index, -1)), ["stop"])
                        }, null, 8, ["disabled", "onClick"]),
                        _createVNode(_component_VBtn, {
                          icon: "mdi-arrow-down",
                          size: "small",
                          variant: "text",
                          disabled: index === config.value.rules.length - 1,
                          title: "下移",
                          onClick: _withModifiers($event => (moveRule(index, 1)), ["stop"])
                        }, null, 8, ["disabled", "onClick"]),
                        _createVNode(_component_VBtn, {
                          icon: "mdi-delete-outline",
                          size: "small",
                          variant: "text",
                          color: "error",
                          title: "删除",
                          onClick: _withModifiers($event => (deleteRule(index)), ["stop"])
                        }, null, 8, ["onClick"])
                      ])
                    ]),
                    _: 2
                  }, 1024),
                  _createVNode(_component_VExpansionPanelText, null, {
                    default: _withCtx(() => [
                      _createVNode(_component_VRow, { class: "pt-2" }, {
                        default: _withCtx(() => [
                          _createVNode(_component_VCol, {
                            cols: "12",
                            md: "6"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_VTextField, {
                                modelValue: rule.mark,
                                "onUpdate:modelValue": $event => ((rule.mark) = $event),
                                label: "备注",
                                clearable: ""
                              }, null, 8, ["modelValue", "onUpdate:modelValue"])
                            ]),
                            _: 2
                          }, 1024),
                          _createVNode(_component_VCol, {
                            cols: "12",
                            md: "6"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_VSelect, {
                                modelValue: rule.downloaders,
                                "onUpdate:modelValue": $event => ((rule.downloaders) = $event),
                                items: downloaderOptions.value,
                                loading: loadingOptions.value,
                                label: "下载器",
                                multiple: "",
                                chips: "",
                                "closable-chips": "",
                                clearable: ""
                              }, null, 8, ["modelValue", "onUpdate:modelValue", "items", "loading"])
                            ]),
                            _: 2
                          }, 1024),
                          _createVNode(_component_VCol, { cols: "12" }, {
                            default: _withCtx(() => [
                              _createVNode(_component_VSelect, {
                                modelValue: rule.limit_sites,
                                "onUpdate:modelValue": $event => ((rule.limit_sites) = $event),
                                items: siteOptions.value,
                                loading: loadingOptions.value,
                                label: "限速站点",
                                multiple: "",
                                chips: "",
                                "closable-chips": "",
                                clearable: ""
                              }, null, 8, ["modelValue", "onUpdate:modelValue", "items", "loading"])
                            ]),
                            _: 2
                          }, 1024),
                          _createVNode(_component_VCol, {
                            cols: "12",
                            md: "3"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_VTextField, {
                                modelValue: rule.limit_speed,
                                "onUpdate:modelValue": $event => ((rule.limit_speed) = $event),
                                modelModifiers: { number: true },
                                label: "上传速度（KB/s）",
                                type: "number",
                                min: "0",
                                hint: "0 表示解除限速",
                                "persistent-hint": ""
                              }, null, 8, ["modelValue", "onUpdate:modelValue"])
                            ]),
                            _: 2
                          }, 1024),
                          _createVNode(_component_VCol, {
                            cols: "12",
                            md: "3"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_VTextField, {
                                modelValue: rule.limit_sites_pause_threshold,
                                "onUpdate:modelValue": $event => ((rule.limit_sites_pause_threshold) = $event),
                                modelModifiers: { number: true },
                                label: "限速暂停（分钟）",
                                type: "number",
                                min: "0",
                                hint: "限速后仍活动时暂停，0 表示不暂停",
                                "persistent-hint": ""
                              }, null, 8, ["modelValue", "onUpdate:modelValue"])
                            ]),
                            _: 2
                          }, 1024),
                          _createVNode(_component_VCol, {
                            cols: "12",
                            md: "6"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_VTextField, {
                                modelValue: rule.active_time_range_site_config,
                                "onUpdate:modelValue": $event => ((rule.active_time_range_site_config) = $event),
                                label: "限速时间段",
                                placeholder: "09:00-02:00；留空表示全天",
                                clearable: ""
                              }, null, 8, ["modelValue", "onUpdate:modelValue"])
                            ]),
                            _: 2
                          }, 1024)
                        ]),
                        _: 2
                      }, 1024)
                    ]),
                    _: 2
                  }, 1024)
                ]),
                _: 2
              }, 1032, ["value"]))
            }), 128))
          ]),
          _: 1
        }, 8, ["modelValue"]),
        _createVNode(_component_VAlert, {
          type: "info",
          variant: "tonal",
          class: "mt-4"
        }, {
          default: _withCtx(() => [...(_cache[12] || (_cache[12] = [
            _createTextVNode(" 规则按列表顺序逐条执行；同一下载器和站点命中多条规则时，靠后的规则最终生效，靠后规则不在限速时间段时会解除前面规则的限速。 ", -1)
          ]))]),
          _: 1
        })
      ]),
      _: 1
    }),
    _createVNode(_component_VSnackbar, {
      modelValue: snackbar.value.show,
      "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((snackbar.value.show) = $event)),
      color: snackbar.value.color,
      timeout: "3500"
    }, {
      default: _withCtx(() => [
        _createTextVNode(_toDisplayString(snackbar.value.text), 1)
      ]),
      _: 1
    }, 8, ["modelValue", "color"])
  ]))
}
}

};
const Config = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-afb5950d"]]);

export { Config as default };
