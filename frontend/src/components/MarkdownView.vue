<script setup>
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

// 所有外部链接（攻略来源、美团预订短链、高德唤端链接）默认新标签页安全打开
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  function (tokens, idx, options, _env, self) {
    return self.renderToken(tokens, idx, options)
  }

md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

const safeHtml = computed(() => {
  if (!props.content) return ''
  const raw = md.render(props.content)
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ['target', 'rel'],
  })
})
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="md-body" v-html="safeHtml"></div>
</template>

<style scoped>
.md-body {
  font-size: 14.5px;
  line-height: 1.72;
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.md-body :deep(> :first-child) {
  margin-top: 0 !important;
}

.md-body :deep(> :last-child) {
  margin-bottom: 0 !important;
}

.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 14px 0 8px;
  font-weight: 600;
  line-height: 1.4;
  color: #111827;
}

.md-body :deep(h1) {
  font-size: 1.3em;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.md-body :deep(h2) {
  font-size: 1.18em;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.md-body :deep(h3) {
  font-size: 1.06em;
}

.md-body :deep(p) {
  margin: 8px 0;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
}

.md-body :deep(li + li) {
  margin-top: 4px;
}

.md-body :deep(strong) {
  font-weight: 600;
  color: #0f172a;
}

.md-body :deep(a) {
  color: var(--el-color-primary);
  text-decoration: none;
  border-bottom: 1px dashed var(--el-color-primary-light-5);
  transition: color 0.15s;
}

.md-body :deep(a:hover) {
  color: var(--el-color-primary-dark-2);
  border-bottom-style: solid;
}

.md-body :deep(blockquote) {
  margin: 10px 0;
  padding: 8px 14px;
  border-left: 4px solid var(--el-color-primary-light-3);
  background: var(--el-color-primary-light-9);
  border-radius: 0 8px 8px 0;
  color: var(--el-text-color-regular);
}

.md-body :deep(code) {
  padding: 2px 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.88em;
  background: var(--el-fill-color);
  color: #c2410c;
  border-radius: 4px;
}

.md-body :deep(pre) {
  margin: 10px 0;
  padding: 12px 14px;
  overflow-x: auto;
  background: #1e293b;
  color: #f8fafc;
  border-radius: 8px;
  line-height: 1.55;
}

.md-body :deep(pre code) {
  padding: 0;
  background: transparent;
  color: inherit;
  font-size: 13px;
}

.md-body :deep(table) {
  display: block;
  width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13.5px;
}

.md-body :deep(th),
.md-body :deep(td) {
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  text-align: left;
}

.md-body :deep(th) {
  background: var(--el-fill-color-light);
  font-weight: 600;
  white-space: nowrap;
}

.md-body :deep(tr:nth-child(even) td) {
  background: var(--el-fill-color-lighter);
}

.md-body :deep(hr) {
  margin: 14px 0;
  border: none;
  border-top: 1px solid var(--el-border-color-lighter);
}
</style>
