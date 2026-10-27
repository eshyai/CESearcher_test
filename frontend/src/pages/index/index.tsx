import { useEffect, useState } from 'react'
import { Input, Tag, Tooltip } from 'antd'
import { ArrowRightOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import * as api from '@/api'
import type { FocusTopic } from '@/api/news'
import { getCurrentIndustry } from '@/store/industry'
import styles from './index.module.scss'

// 示例检索词：都是化工从业者日常真的会去查的碎片信息，点一下就能看到效果
const EXAMPLES = ['万华化学 MDI 扩产', '己内酰胺 价格', '危化品 监管新规', 'PA66 产能']

// 功能入口：保留的四条主路径，顺序按使用频率排
const ENTRIES = [
  {
    key: 'news',
    title: '行业资讯',
    desc: '按分类与事件类型浏览，支持屏蔽噪声、给单条写备注',
    href: '/news',
  },
  {
    key: 'topics',
    title: '我的关注',
    desc: '产品 / 企业 / 工艺主题；给资讯的反馈越多，排序越贴合你',
    href: '/topics',
  },
  {
    key: 'chat',
    title: '深度研究',
    desc: '六个智能体协作，产出一份带来源引用的调研报告',
    href: '/chat',
  },
  {
    key: 'knowledge',
    title: '知识库',
    desc: '上传自己的文档，研究时一并召回',
    href: '/knowledge',
  },
]

export default function Index() {
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [topics, setTopics] = useState<FocusTopic[]>([])
  const [topicsLoading, setTopicsLoading] = useState(true)
  const currentIndustry = getCurrentIndustry()

  // 关注主题：做成快捷检索词，比让用户自己想关键词更省事
  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const list = await api.news.getFocusTopics()
        if (!alive) return
        setTopics((list || []).filter((t) => t.is_active).slice(0, 8))
      } catch (e) {
        // 拉不到关注主题不影响检索主路径，静默降级（检索框照常可用）
        console.warn('[Index] 获取关注主题失败，已跳过快捷主题', e)
      } finally {
        if (alive) setTopicsLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  // 检索统一走 /news：结果页、反馈、备注、屏蔽都在那边，首页只负责「把查询交出去」
  const goSearch = (raw?: string) => {
    const q = (raw ?? keyword).trim()
    if (!q) {
      navigate('/news')
      return
    }
    navigate(`/news?q=${encodeURIComponent(q)}`)
  }

  return (
    <div className={styles['index-page']}>
      <div className={styles.hero}>
        <div className={styles['hero__eyebrow']}>
          <span className={styles['hero__industry']}>{currentIndustry.name}</span>
          <span className={styles['hero__eyebrow-text']}>行业资讯检索</span>
        </div>

        <h1 className={styles['hero__title']}>一段话，找到相关报道</h1>
        <p className={styles['hero__desc']}>
          输入公司名、产品、工艺或政策文号——比如「万华化学 MDI 扩产」「危化品监管新规」，
          就能拿到库里已有和全网最新的相关报道。
        </p>

        <div className={styles['hero__search']}>
          <Input.Search
            size="large"
            allowClear
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={(value) => goSearch(value)}
            enterButton="检索"
            placeholder="公司 / 产品 / 工艺 / 政策片段…"
          />
        </div>

        <div className={styles['hero__examples']}>
          <span className={styles['hero__examples-label']}>试试</span>
          {EXAMPLES.map((word) => (
            <button
              key={word}
              type="button"
              className={styles['example-chip']}
              onClick={() => goSearch(word)}
            >
              {word}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.section}>
        <div className={styles['section__head']}>
          <span className={styles['section__title']}>我的关注</span>
          <a className={styles['section__more']} onClick={() => navigate('/topics')}>
            管理关注
            <ArrowRightOutlined />
          </a>
        </div>
        {topicsLoading ? (
          <div className={styles['section__hint']}>正在读取关注主题…</div>
        ) : topics.length > 0 ? (
          <div className={styles['topic-list']}>
            {topics.map((topic) => (
              <Tooltip key={topic.id} title={topic.query_text || topic.name}>
                <Tag
                  className={styles['topic-tag']}
                  onClick={() => goSearch(topic.query_text || topic.name)}
                >
                  {topic.name}
                </Tag>
              </Tooltip>
            ))}
          </div>
        ) : (
          <div className={styles['section__hint']}>
            还没有关注主题。去
            <a onClick={() => navigate('/topics')}>「我的关注」</a>
            添加产品、企业或工艺，之后的资讯会按它们排序。
          </div>
        )}
      </div>

      <div className={styles.entries}>
        {ENTRIES.map((item) => (
          <div
            key={item.key}
            className={styles['entry-card']}
            onClick={() => navigate(item.href)}
          >
            <div className={styles['entry-card__title']}>
              {item.title}
              <ArrowRightOutlined className={styles['entry-card__arrow']} />
            </div>
            <div className={styles['entry-card__desc']}>{item.desc}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
