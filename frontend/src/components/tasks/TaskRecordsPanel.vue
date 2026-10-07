<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import {
  ChatDotRound,
  CircleCheck,
  Clock,
  Download,
  MagicStick,
  Tickets,
  WarningFilled,
} from '@element-plus/icons-vue'
import PageState from '@/components/PageState.vue'
import type { TaskRecord, TaskStage, TaskStatus } from '@/types'
import { taskDuration, taskSummary, taskTime } from '@/utils/taskPresentation'

const props = defineProps<{ tasks: TaskRecord[]; stages: TaskStage[] }>()
const stage = shallowRef<TaskStage | '全部'>('全部')
const rows = computed(() =>
  props.tasks.filter((task) => stage.value === '全部' || task.stage === stage.value),
)
const stageIcons = { 采集: Download, 富化: MagicStick, 审核: CircleCheck, 问答: ChatDotRound }
const statusClass: Record<TaskStatus, string> = {
  已完成: 'success',
  待处理: 'pending',
  失败: 'failed',
  未接入: 'disconnected',
}
</script>

<template>
  <section class="panel task-records-panel">
    <div class="panel-title">
      <div>
        <h2><Tickets class="section-icon" aria-hidden="true" />任务记录</h2>
        <p>状态和时间来自后端任务记录</p>
      </div>
      <div class="task-filters">
        <label for="task-stage">任务阶段</label>
        <select id="task-stage" v-model="stage" class="native-select">
          <option value="全部">全部阶段</option>
          <option v-for="item in stages" :key="item" :value="item">{{ item }}</option>
        </select>
      </div>
    </div>
    <PageState :empty="rows.length === 0" empty-text="当前阶段暂无任务记录。">
      <div class="task-list">
        <article v-for="task in rows" :key="task.id" class="task-row">
          <span :class="['task-icon', `stage-${task.stage}`]" aria-hidden="true">
            <component :is="stageIcons[task.stage]" />
          </span>
          <div class="task-content">
            <div class="task-top">
              <strong>{{ task.name }}</strong>
              <span :class="['task-status', statusClass[task.status]]">{{ task.status }}</span>
            </div>
              <p>{{ taskSummary(task.detail) }}</p>
              <details class="task-raw"><summary>查看原始任务结果</summary><pre>{{ task.detail }}</pre></details>
            <div class="task-meta">
              <span>{{ task.id }}</span>
              <span class="task-stage-tag">{{ task.stage }}阶段</span>
              <span class="task-time">
                <Clock aria-hidden="true" />
                <time v-if="task.time" :datetime="task.time.replace(' ', 'T')">{{
                  taskTime(task.time)
                }}</time>
                <span v-else>暂无数据</span>
              </span>
              <span v-if="taskDuration(task)">创建至完成 {{ taskDuration(task) }}</span>
            </div>
            <div v-if="task.error" class="task-error">
              <WarningFilled aria-hidden="true" />
              <span>{{ task.error }}</span>
            </div>
          </div>
        </article>
      </div>
    </PageState>
  </section>
</template>

<style scoped>
.task-raw { margin: 8px 0 12px; color: #667085; font-size: 11px; }
.task-raw summary { cursor: pointer; }
.task-raw pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 180px; overflow: auto; }
.task-raw summary:focus-visible { outline: 2px solid #4262ed; outline-offset: 3px; }
</style>
