<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'PTDownloaderLimit' },
  initialConfig: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['save', 'close', 'layout'])

const config = ref(defaultConfig())
const downloaderOptions = ref([])
const siteOptions = ref([])
const expanded = ref(0)
const loadingOptions = ref(false)
const snackbar = ref({ show: false, text: '', color: 'info' })

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
  const result = { ...defaultConfig(), ...clone(value) }
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
    : [emptyRule()]
  return result
}

const pluginBase = computed(() => `plugin/${props.pluginId || 'PTDownloaderLimit'}`)

function showMessage(text, color = 'info') {
  snackbar.value = { show: true, text, color }
}

function unwrapResponse(response) {
  if (response?.data?.success !== undefined) return response.data
  if (response?.success !== undefined) return response
  return response?.data || response || {}
}

async function loadOptions() {
  if (!props.api?.get) return
  loadingOptions.value = true
  try {
    const response = unwrapResponse(await props.api.get(`${pluginBase.value}/options`))
    if (!response?.success) throw new Error(response?.message || '加载选项失败')
    downloaderOptions.value = response.data?.downloaders || []
    siteOptions.value = response.data?.sites || []
  } catch (error) {
    showMessage(error?.message || '下载器和站点选项加载失败', 'error')
  } finally {
    loadingOptions.value = false
  }
}

function addRule() {
  config.value.rules.push(emptyRule())
  nextTick(() => {
    expanded.value = config.value.rules.length - 1
  })
}

function deleteRule(index) {
  config.value.rules.splice(index, 1)
  if (!config.value.rules.length) {
    expanded.value = undefined
  } else if (expanded.value >= config.value.rules.length) {
    expanded.value = config.value.rules.length - 1
  }
}

function moveRule(index, offset) {
  const target = index + offset
  if (target < 0 || target >= config.value.rules.length) return
  const [rule] = config.value.rules.splice(index, 1)
  config.value.rules.splice(target, 0, rule)
  expanded.value = target
}

function chineseNumber(value) {
  const digits = '零一二三四五六七八九'
  if (value < 10) return digits[value]
  if (value < 20) return `十${value % 10 ? digits[value % 10] : ''}`
  if (value < 100) return `${digits[Math.floor(value / 10)]}十${value % 10 ? digits[value % 10] : ''}`
  return String(value)
}

function ruleTitle(index, rule) {
  const title = `限速${chineseNumber(index + 1)}`
  return rule.mark?.trim() ? `${title}：${rule.mark.trim()}` : title
}

function validate() {
  const timePattern = /^\d{2}:\d{2}-\d{2}:\d{2}$/
  const validClock = value => {
    const [hour, minute] = value.split(':').map(Number)
    return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59
  }
  for (let index = 0; index < config.value.rules.length; index += 1) {
    const rule = config.value.rules[index]
    const range = rule.active_time_range_site_config?.trim()
    const clocks = range?.split('-') || []
    if (range && (!timePattern.test(range) || !clocks.every(validClock))) {
      expanded.value = index
      showMessage(`限速${chineseNumber(index + 1)}的时间段格式应为 HH:MM-HH:MM`, 'error')
      return false
    }
    if (Number(rule.limit_speed) < 0 || Number(rule.limit_sites_pause_threshold) < 0) {
      expanded.value = index
      showMessage(`限速${chineseNumber(index + 1)}的速度和暂停分钟不能小于 0`, 'error')
      return false
    }
  }
  return true
}

function saveConfig() {
  if (!validate()) return
  const payload = clone(config.value)
  payload.rules = payload.rules.map(rule => ({
    ...rule,
    limit_speed: Math.max(0, Number(rule.limit_speed || 0)),
    limit_sites_pause_threshold: Math.max(0, Number(rule.limit_sites_pause_threshold || 0)),
    active_time_range_site_config: String(rule.active_time_range_site_config || '').trim(),
  }))
  emit('save', payload)
  showMessage('配置已提交保存', 'success')
}

onMounted(() => {
  emit('layout', { maxWidth: '90rem' })
  config.value = normalizeConfig(props.initialConfig)
  expanded.value = config.value.rules.length ? 0 : undefined
  loadOptions()
})
</script>

<template>
  <div class="pt-limit-config">
    <VToolbar density="comfortable" color="transparent">
      <div class="text-h6 ms-3">QB&TR上传限速 - 插件配置</div>
      <VSpacer />
      <VBtn icon="mdi-content-save" variant="text" color="primary" @click="saveConfig" />
      <VBtn icon="mdi-close" variant="text" @click="emit('close')" />
    </VToolbar>
    <VDivider />

    <VForm class="pa-4" @submit.prevent="saveConfig">
      <VRow>
        <VCol cols="12" md="2">
          <VSwitch v-model="config.enabled" label="启用插件" color="primary" hide-details />
        </VCol>
        <VCol cols="12" md="2">
          <VCronField v-model="config.cron" label="执行周期" />
        </VCol>
        <VCol cols="12" md="3">
          <VTextField
            v-model="config.nolabels"
            label="不限速标签"
            placeholder="多个标签使用英文逗号分隔"
            clearable
          />
        </VCol>
        <VCol cols="6" md="2">
          <VSwitch v-model="config.notify" label="开启通知" color="primary" hide-details />
        </VCol>
        <VCol cols="6" md="3">
          <VSwitch v-model="config.onlyonce" label="立即运行一次" color="primary" hide-details />
        </VCol>
      </VRow>

      <div class="d-flex align-center mt-4 mb-3">
        <div class="text-h6">限速规则</div>
        <VSpacer />
        <VBtn color="primary" prepend-icon="mdi-plus" variant="tonal" @click="addRule">
          新增规则
        </VBtn>
      </div>

      <VAlert v-if="!config.rules.length" type="info" variant="tonal" class="mb-4">
        当前没有限速规则，可点击“新增规则”开始配置。
      </VAlert>

      <VExpansionPanels v-model="expanded" variant="accordion" class="rule-panels">
        <VExpansionPanel v-for="(rule, index) in config.rules" :key="rule.id" :value="index">
          <VExpansionPanelTitle>
            <div class="d-flex align-center w-100 pe-2 rule-title">
              <span class="font-weight-medium text-truncate">{{ ruleTitle(index, rule) }}</span>
              <VSpacer />
              <VBtn
                icon="mdi-arrow-up"
                size="small"
                variant="text"
                :disabled="index === 0"
                title="上移"
                @click.stop="moveRule(index, -1)"
              />
              <VBtn
                icon="mdi-arrow-down"
                size="small"
                variant="text"
                :disabled="index === config.rules.length - 1"
                title="下移"
                @click.stop="moveRule(index, 1)"
              />
              <VBtn
                icon="mdi-delete-outline"
                size="small"
                variant="text"
                color="error"
                title="删除"
                @click.stop="deleteRule(index)"
              />
            </div>
          </VExpansionPanelTitle>
          <VExpansionPanelText>
            <VRow class="pt-2">
              <VCol cols="12" md="6">
                <VTextField v-model="rule.mark" label="备注" clearable />
              </VCol>
              <VCol cols="12" md="6">
                <VSelect
                  v-model="rule.downloaders"
                  :items="downloaderOptions"
                  :loading="loadingOptions"
                  label="下载器"
                  multiple
                  chips
                  closable-chips
                  clearable
                />
              </VCol>
              <VCol cols="12">
                <VSelect
                  v-model="rule.limit_sites"
                  :items="siteOptions"
                  :loading="loadingOptions"
                  label="限速站点"
                  multiple
                  chips
                  closable-chips
                  clearable
                />
              </VCol>
              <VCol cols="12" md="3">
                <VTextField
                  v-model.number="rule.limit_speed"
                  label="上传速度（KB/s）"
                  type="number"
                  min="0"
                  hint="0 表示解除限速"
                  persistent-hint
                />
              </VCol>
              <VCol cols="12" md="3">
                <VTextField
                  v-model.number="rule.limit_sites_pause_threshold"
                  label="限速暂停（分钟）"
                  type="number"
                  min="0"
                  hint="限速后仍活动时暂停，0 表示不暂停"
                  persistent-hint
                />
              </VCol>
              <VCol cols="12" md="6">
                <VTextField
                  v-model="rule.active_time_range_site_config"
                  label="限速时间段"
                  placeholder="09:00-02:00；留空表示全天"
                  clearable
                />
              </VCol>
            </VRow>
          </VExpansionPanelText>
        </VExpansionPanel>
      </VExpansionPanels>

      <VAlert type="info" variant="tonal" class="mt-4">
        规则按列表顺序逐条执行；同一下载器和站点命中多条规则时，靠后的规则最终生效，靠后规则不在限速时间段时会解除前面规则的限速。
      </VAlert>
    </VForm>

    <VSnackbar v-model="snackbar.show" :color="snackbar.color" timeout="3500">
      {{ snackbar.text }}
    </VSnackbar>
  </div>
</template>

<style scoped>
.pt-limit-config {
  width: 100%;
}

.rule-panels :deep(.v-expansion-panel) {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  margin-bottom: 10px;
}

.rule-title {
  min-width: 0;
}

@media (max-width: 600px) {
  .rule-panels :deep(.v-expansion-panel-title) {
    padding-inline: 8px;
  }

  .rule-title :deep(.v-btn) {
    margin-inline: -2px;
    width: 32px;
    height: 32px;
  }
}
</style>
