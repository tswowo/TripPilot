<script setup>
import {
  ChatDotRound,
  CircleCheck,
  Close,
  Document,
  Files,
  Loading,
  Refresh,
  Upload,
  View,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { api } from '../api'
import MarkdownView from './MarkdownView.vue'

const emit = defineEmits(['close', 'logout'])
const MAX_FILE_BYTES = 10 * 1024 * 1024
const PAGE_SIZE = 20

const STATUS_META = {
  pending: { label: '待向量化', type: 'info' },
  processing: { label: '向量化中', type: 'primary' },
  completed: { label: '已向量化', type: 'success' },
  failed: { label: '向量化失败', type: 'danger' },
}

const guides = ref([])
const nextCursor = ref(null)
const hasMore = ref(false)
const loading = ref(false)
const loadingMore = ref(false)
const loadError = ref('')

const uploadVisible = ref(false)
const uploading = ref(false)
const selectedFile = ref(null)
const uploadForm = reactive({ title: '', city: '', category: '' })

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

const triggeringId = ref(null)
const deletingId = ref(null)
const pollingIds = ref([])
let pollTimer = null

const stats = computed(() => ({
  total: guides.value.length,
  completed: guides.value.filter((guide) => guide.vector_status === 'completed').length,
  processing: guides.value.filter((guide) => guide.vector_status === 'processing').length,
}))

onMounted(loadGuides)
onBeforeUnmount(stopPolling)

async function loadGuides(reset = false) {
  if (!reset && loadingMore.value) return
  if (reset) {
    loading.value = true
  } else {
    loadingMore.value = true
  }
  loadError.value = ''
  try {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) })
    if (!reset && nextCursor.value) params.set('cursor', nextCursor.value)
    const data = await api(`/guides?${params.toString()}`)
    const incoming = data.items || []
    if (reset) {
      guides.value = incoming
    } else {
      const seen = new Set(guides.value.map((guide) => guide.doc_id))
      guides.value.push(...incoming.filter((guide) => !seen.has(guide.doc_id)))
    }
    nextCursor.value = data.next_cursor
    hasMore.value = data.has_more
    syncPolling()
  } catch (error) {
    if (error.status === 401) {
      emit('logout')
      return
    }
    loadError.value = error.message
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

function syncPolling() {
  const activeIds = guides.value
    .filter((guide) => guide.vector_status === 'processing')
    .map((guide) => guide.doc_id)
  pollingIds.value = [...new Set([...pollingIds.value, ...activeIds])]
  if (pollingIds.value.length > 0) {
    startPolling()
  } else {
    stopPolling()
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = window.setInterval(pollDocuments, 2000)
}

function stopPolling() {
  if (!pollTimer) return
  window.clearInterval(pollTimer)
  pollTimer = null
}

async function pollDocuments() {
  const ids = [...pollingIds.value]
  if (ids.length === 0) {
    stopPolling()
    return
  }

  await Promise.all(
    ids.map(async (docId) => {
      try {
        const updated = await api(`/guides/${encodeURIComponent(docId)}`)
        updateGuide(updated)
        if (!['pending', 'processing'].includes(updated.vector_status)) {
          pollingIds.value = pollingIds.value.filter((id) => id !== docId)
        }
      } catch (error) {
        if (error.status === 401) {
          stopPolling()
          emit('logout')
        }
      }
    }),
  )

  if (pollingIds.value.length === 0) stopPolling()
}

function updateGuide(updated) {
  const index = guides.value.findIndex((guide) => guide.doc_id === updated.doc_id)
  if (index >= 0) {
    guides.value[index] = updated
  }
  if (detail.value?.doc_id === updated.doc_id) {
    detail.value = { ...detail.value, ...updated }
  }
}

function openUpload() {
  uploadVisible.value = true
}

function resetUpload() {
  selectedFile.value = null
  uploadForm.title = ''
  uploadForm.city = ''
  uploadForm.category = ''
}

function selectFile(uploadFile) {
  selectedFile.value = null
  const file = uploadFile?.raw
  if (!file) return
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (!['md', 'txt'].includes(extension)) {
    ElMessage.error('仅支持 .md 或 .txt 文件')
    return
  }
  if (file.size > MAX_FILE_BYTES) {
    ElMessage.error('文件不能超过 10MB')
    return
  }
  selectedFile.value = file
}

function rejectExtraFile() {
  ElMessage.warning('一次只能上传一个文件')
}

async function uploadGuide() {
  if (!selectedFile.value || uploading.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    if (uploadForm.title.trim()) formData.append('title', uploadForm.title.trim())
    if (uploadForm.city.trim()) formData.append('city', uploadForm.city.trim())
    if (uploadForm.category) formData.append('category', uploadForm.category)

    await api('/guides/upload', formData)
    uploadVisible.value = false
    resetUpload()
    ElMessage.success('上传成功，请手动触发向量化')
    await loadGuides(true)
  } catch (error) {
    if (error.status === 401) {
      emit('logout')
      return
    }
    ElMessage.error(error.message)
  } finally {
    uploading.value = false
  }
}

async function openDetail(guide) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = guide
  try {
    detail.value = await api(`/guides/${encodeURIComponent(guide.doc_id)}`)
  } catch (error) {
    if (error.status === 401) {
      detailVisible.value = false
      emit('logout')
      return
    }
    ElMessage.error(error.message)
  } finally {
    detailLoading.value = false
  }
}

async function triggerVectorization(guide) {
  if (triggeringId.value) return
  triggeringId.value = guide.doc_id
  try {
    const updated = await api(
      `/guides/${encodeURIComponent(guide.doc_id)}/vectorize`,
      undefined,
      'POST',
    )
    updateGuide(updated)
    if (!pollingIds.value.includes(guide.doc_id)) pollingIds.value.push(guide.doc_id)
    startPolling()
    ElMessage.success('向量化任务已触发')
  } catch (error) {
    if (error.status === 401) {
      emit('logout')
      return
    }
    if (error.status === 409) {
      await loadGuides(true)
    }
    ElMessage.error(error.message)
  } finally {
    triggeringId.value = null
  }
}

async function confirmDelete(guide) {
  if (deletingId.value) return
  try {
    await ElMessageBox.confirm(
      `删除「${guide.title}」后，文档及其向量数据将无法恢复。`,
      '删除攻略',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }

  deletingId.value = guide.doc_id
  try {
    await api(`/guides/${encodeURIComponent(guide.doc_id)}`, undefined, 'DELETE')
    guides.value = guides.value.filter((item) => item.doc_id !== guide.doc_id)
    pollingIds.value = pollingIds.value.filter((id) => id !== guide.doc_id)
    if (detail.value?.doc_id === guide.doc_id) detailVisible.value = false
    if (guides.value.length < 10 && hasMore.value) loadGuides()
    ElMessage.success('攻略已删除')
  } catch (error) {
    if (error.status === 401) {
      emit('logout')
      return
    }
    if (error.status === 409) {
      await loadGuides(true)
    }
    ElMessage.error(error.message)
  } finally {
    deletingId.value = null
  }
}

function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.pending
}

function progressStatus(status) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'exception'
  return undefined
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatFileSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="guide-library">
    <header class="library-header">
      <div>
        <div class="title-row">
          <span class="title-icon"
            ><el-icon :size="20"><Files /></el-icon
          ></span>
          <h2>攻略知识库</h2>
        </div>
        <p>上传旅行经验，手动构建可检索的城市知识。</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Upload" type="primary" @click="openUpload">上传攻略</el-button>
        <button type="button" class="close-button" title="关闭" @click="emit('close')">
          <el-icon :size="18"><Close /></el-icon>
        </button>
      </div>
    </header>

    <section class="overview" aria-label="攻略统计">
      <div class="overview-item">
        <span class="overview-icon blue"
          ><el-icon><Document /></el-icon
        ></span>
        <div>
          <strong>{{ stats.total }}</strong
          ><span>当前文档</span>
        </div>
      </div>
      <div class="overview-item">
        <span class="overview-icon green"
          ><el-icon><CircleCheck /></el-icon
        ></span>
        <div>
          <strong>{{ stats.completed }}</strong
          ><span>已向量化</span>
        </div>
      </div>
      <div class="overview-item">
        <span class="overview-icon orange"
          ><el-icon><Loading /></el-icon
        ></span>
        <div>
          <strong>{{ stats.processing }}</strong
          ><span>处理中</span>
        </div>
      </div>
      <div class="overview-note">
        <el-icon><ChatDotRound /></el-icon>
        <span>上传不会自动调用 Embedding，请在文档列表中手动触发。</span>
      </div>
    </section>

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      show-icon
      :closable="false"
      class="load-error"
    >
      <el-button text type="primary" @click="loadGuides(true)">重新加载</el-button>
    </el-alert>

    <div v-loading="loading" class="guide-list">
      <el-empty
        v-if="!loading && !loadError && guides.length === 0"
        :image-size="88"
        description="还没有攻略文档"
      >
        <el-button type="primary" :icon="Upload" @click="openUpload">上传第一篇攻略</el-button>
      </el-empty>

      <article v-for="guide in guides" :key="guide.doc_id" class="guide-card">
        <div class="guide-main">
          <div class="guide-heading">
            <div class="guide-title-wrap">
              <h3 :title="guide.title">{{ guide.title }}</h3>
              <div class="guide-tags">
                <el-tag v-if="guide.city" size="small" effect="plain">{{ guide.city }}</el-tag>
                <el-tag v-if="guide.category" size="small" type="info" effect="plain">
                  {{ guide.category }}
                </el-tag>
              </div>
            </div>
            <el-tag :type="statusMeta(guide.vector_status).type" effect="light" round>
              {{ statusMeta(guide.vector_status).label }}
            </el-tag>
          </div>

          <p v-if="guide.summary" class="guide-summary">{{ guide.summary }}</p>

          <div class="progress-block">
            <div class="progress-label">
              <span>向量化进度</span>
              <span>
                {{ guide.vector_completed_chunks }}/{{ guide.vector_total_chunks }} Chunks
                <strong>{{ guide.vector_progress }}%</strong>
              </span>
            </div>
            <el-progress
              :percentage="guide.vector_progress"
              :status="progressStatus(guide.vector_status)"
              :stroke-width="7"
              :show-text="false"
            />
          </div>

          <el-alert
            v-if="guide.vector_status === 'failed' && guide.vector_error"
            :title="guide.vector_error"
            type="error"
            :closable="false"
            show-icon
            class="vector-error"
          />

          <div class="guide-meta">
            <span>{{ guide.original_filename || '未知文件' }}</span>
            <span>{{ formatFileSize(guide.file_size) }}</span>
            <span>更新于 {{ formatTime(guide.update_time) }}</span>
          </div>
        </div>

        <div class="guide-actions">
          <el-button text :icon="View" @click="openDetail(guide)">查看详情</el-button>
          <el-button
            v-if="['pending', 'failed'].includes(guide.vector_status)"
            type="primary"
            plain
            :icon="Refresh"
            :loading="triggeringId === guide.doc_id"
            @click="triggerVectorization(guide)"
          >
            {{ guide.vector_status === 'failed' ? '重试向量化' : '开始向量化' }}
          </el-button>
          <el-button
            type="danger"
            text
            :icon="Close"
            :disabled="guide.vector_status === 'processing'"
            :loading="deletingId === guide.doc_id"
            @click="confirmDelete(guide)"
          >
            删除
          </el-button>
        </div>
      </article>

      <div v-if="hasMore" class="load-more">
        <el-button :loading="loadingMore" @click="loadGuides()">加载更多</el-button>
      </div>
    </div>

    <el-dialog
      v-model="uploadVisible"
      title="上传攻略"
      width="560px"
      :close-on-click-modal="!uploading"
      :close-on-press-escape="!uploading"
      @closed="resetUpload"
    >
      <el-upload
        class="upload-area"
        drag
        accept=".md,.txt"
        :limit="1"
        :auto-upload="false"
        :show-file-list="false"
        :disabled="uploading"
        @change="selectFile"
        @exceed="rejectExtraFile"
      >
        <el-icon class="upload-icon" :size="34"><Upload /></el-icon>
        <div class="upload-title">将文件拖到此处，或<em>点击选择</em></div>
        <div class="upload-tip">仅支持 UTF-8 编码的 .md / .txt，单个文件不超过 10MB</div>
      </el-upload>

      <div v-if="selectedFile" class="selected-file">
        <el-icon><Document /></el-icon>
        <span :title="selectedFile.name">{{ selectedFile.name }}</span>
        <span>{{ formatFileSize(selectedFile.size) }}</span>
        <el-button text type="primary" :disabled="uploading" @click="selectedFile = null"
          >更换</el-button
        >
      </div>

      <el-form label-position="top" class="upload-form">
        <el-form-item label="标题（可选）">
          <el-input
            v-model="uploadForm.title"
            maxlength="200"
            show-word-limit
            placeholder="留空则使用文件名"
          />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="城市（可选）">
            <el-input v-model="uploadForm.city" maxlength="100" placeholder="例如：南京" />
          </el-form-item>
          <el-form-item label="分类（可选）">
            <el-select
              v-model="uploadForm.category"
              clearable
              placeholder="选择分类"
              style="width: 100%"
            >
              <el-option label="景区规则" value="景区规则" />
              <el-option label="美食避坑" value="美食避坑" />
              <el-option label="动线指南" value="动线指南" />
              <el-option label="其他" value="其他" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>

      <el-alert
        title="上传后状态为“待向量化”，不会自动产生 Embedding 费用。"
        type="info"
        :closable="false"
        show-icon
      />

      <template #footer>
        <el-button :disabled="uploading" @click="uploadVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!selectedFile"
          @click="uploadGuide"
        >
          上传并保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" title="攻略详情" width="880px" top="6vh">
      <div v-if="detail" v-loading="detailLoading" class="detail-body">
        <div class="detail-heading">
          <div>
            <h2>{{ detail.title }}</h2>
            <div class="guide-tags">
              <el-tag v-if="detail.city" effect="plain">{{ detail.city }}</el-tag>
              <el-tag v-if="detail.category" type="info" effect="plain">{{
                detail.category
              }}</el-tag>
              <el-tag :type="statusMeta(detail.vector_status).type" effect="light">
                {{ statusMeta(detail.vector_status).label }}
              </el-tag>
            </div>
          </div>
          <el-button
            v-if="['pending', 'failed'].includes(detail.vector_status)"
            type="primary"
            :icon="Refresh"
            :loading="triggeringId === detail.doc_id"
            @click="triggerVectorization(detail)"
          >
            {{ detail.vector_status === 'failed' ? '重试向量化' : '开始向量化' }}
          </el-button>
        </div>

        <el-descriptions :column="2" border class="detail-descriptions">
          <el-descriptions-item label="原始文件">{{
            detail.original_filename || '—'
          }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{
            formatFileSize(detail.file_size)
          }}</el-descriptions-item>
          <el-descriptions-item label="向量化进度">
            {{ detail.vector_completed_chunks }}/{{ detail.vector_total_chunks }}（{{
              detail.vector_progress
            }}%）
          </el-descriptions-item>
          <el-descriptions-item label="上传时间">{{
            formatTime(detail.create_time)
          }}</el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="detail.vector_error"
          :title="detail.vector_error"
          type="error"
          :closable="false"
          show-icon
          class="detail-error"
        />

        <div class="detail-section-title">文档正文</div>
        <div class="markdown-preview">
          <MarkdownView :content="detail.content" />
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.guide-library {
  min-height: 100%;
  padding: 4px 2px 24px;
  color: #1f2329;
}

.library-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-icon {
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.library-header h2 {
  margin: 0;
  font-size: 21px;
  color: #0f172a;
}

.library-header p {
  margin: 8px 0 0 46px;
  color: #64748b;
  font-size: 13px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.close-button {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.close-button:hover {
  background: #f1f5f9;
  color: #0f172a;
}

.overview {
  display: grid;
  grid-template-columns: repeat(3, minmax(120px, 1fr)) minmax(240px, 1.7fr);
  gap: 12px;
  margin-bottom: 18px;
}

.overview-item,
.overview-note {
  min-height: 76px;
  border: 1px solid #e5eaf3;
  border-radius: 13px;
  background: #fff;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.overview-icon {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 11px;
}

.overview-icon.blue {
  color: #2563eb;
  background: #eff6ff;
}

.overview-icon.green {
  color: #16a34a;
  background: #f0fdf4;
}

.overview-icon.orange {
  color: #ea580c;
  background: #fff7ed;
}

.overview-item strong {
  display: block;
  font-size: 21px;
  line-height: 1.15;
  color: #0f172a;
}

.overview-item span:not(.overview-icon) {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}

.overview-note {
  background: #f8fafc;
  color: #64748b;
  font-size: 12.5px;
  line-height: 1.6;
}

.overview-note .el-icon {
  flex-shrink: 0;
  color: #3b82f6;
  font-size: 18px;
}

.load-error {
  margin-bottom: 16px;
}

.guide-list {
  min-height: 260px;
}

.guide-card {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 12px;
  padding: 18px 20px;
  border: 1px solid #e5eaf3;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 3px 12px rgb(15 23 42 / 3%);
  transition:
    border-color 0.15s,
    box-shadow 0.15s;
}

.guide-card:hover {
  border-color: #cbd5e1;
  box-shadow: 0 6px 18px rgb(15 23 42 / 6%);
}

.guide-main {
  flex: 1;
  min-width: 0;
}

.guide-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.guide-title-wrap {
  min-width: 0;
}

.guide-title-wrap h3 {
  margin: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 16px;
  font-weight: 650;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.guide-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.guide-summary {
  display: -webkit-box;
  margin: 11px 0 13px;
  overflow: hidden;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.progress-block {
  max-width: 640px;
}

.progress-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 7px;
  color: #64748b;
  font-size: 12px;
}

.progress-label strong {
  margin-left: 8px;
  color: #334155;
}

.vector-error {
  margin-top: 12px;
}

.guide-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
  color: #94a3b8;
  font-size: 11.5px;
}

.guide-actions {
  width: 132px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: center;
  gap: 7px;
  padding-left: 18px;
  border-left: 1px solid #eef2f7;
}

.load-more {
  display: flex;
  justify-content: center;
  padding: 12px 0;
}

.upload-area :deep(.el-upload-dragger) {
  padding: 24px 16px;
  border-radius: 12px;
  background: #f8fafc;
}

.upload-icon {
  color: #3b82f6;
}

.upload-title {
  margin-top: 10px;
  color: #334155;
  font-size: 14px;
}

.upload-title em {
  color: var(--el-color-primary);
  font-style: normal;
}

.upload-tip {
  margin-top: 7px;
  color: #94a3b8;
  font-size: 12px;
}

.selected-file {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #dbeafe;
  border-radius: 10px;
  background: #eff6ff;
  color: #334155;
  font-size: 13px;
}

.selected-file span:nth-of-type(1) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-file span:nth-of-type(2) {
  flex-shrink: 0;
  color: #64748b;
  font-size: 12px;
}

.upload-form {
  margin-top: 18px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.detail-body {
  min-height: 280px;
}

.detail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 18px;
}

.detail-heading h2 {
  margin: 0;
  color: #0f172a;
  font-size: 22px;
}

.detail-descriptions {
  margin-bottom: 18px;
}

.detail-error {
  margin-bottom: 18px;
}

.detail-section-title {
  margin-bottom: 10px;
  color: #334155;
  font-size: 14px;
  font-weight: 600;
}

.markdown-preview {
  max-height: 56vh;
  overflow-y: auto;
  padding: 18px 20px;
  border: 1px solid #e5eaf3;
  border-radius: 12px;
  background: #fff;
}

@media (max-width: 860px) {
  .overview {
    grid-template-columns: repeat(3, 1fr);
  }

  .overview-note {
    grid-column: 1 / -1;
    min-height: auto;
  }

  .guide-card {
    flex-direction: column;
  }

  .guide-actions {
    width: 100%;
    flex-direction: row;
    justify-content: flex-end;
    padding: 12px 0 0;
    border-top: 1px solid #eef2f7;
    border-left: none;
  }
}

@media (max-width: 560px) {
  .library-header {
    flex-direction: column;
  }

  .header-actions {
    width: 100%;
    justify-content: space-between;
  }

  .library-header p {
    margin-left: 0;
  }

  .overview {
    grid-template-columns: 1fr;
  }

  .overview-note {
    grid-column: auto;
  }

  .form-row {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .guide-heading,
  .detail-heading {
    flex-direction: column;
  }
}
</style>
