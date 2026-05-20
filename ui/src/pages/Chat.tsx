/**
 * Chat page — send a message to an agent and poll for the response.
 * State is persisted in sessionStorage so navigation doesn't lose the session.
 */
import { useState, useRef, useEffect, FormEvent } from 'react'
import { Send, Bot, User, Loader2, CheckCircle, XCircle, AlertTriangle, Trash2 } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api/client'

const SESSION_KEY = 'agentix_chat_session'

interface Message {
  id: string
  role: 'user' | 'agent'
  text: string
  ts: number
}

interface ChatSession {
  agentId: string
  messages: Message[]
  pendingTrigger: string | null
  pendingStatus: string
}

function loadSession(): ChatSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveSession(s: ChatSession) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(s))
  } catch {}
}

function clearSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY)
  } catch {}
}

export default function Chat() {
  const saved = loadSession()

  const [agentId, setAgentId] = useState(saved?.agentId ?? '')
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>(saved?.messages ?? [])
  const [pendingTrigger, setPendingTrigger] = useState<string | null>(saved?.pendingTrigger ?? null)
  const [pendingStatus, setPendingStatus] = useState<string>(saved?.pendingStatus ?? 'queued')
  const [sending, setSending] = useState(false)
  const [hitlData, setHitlData] = useState<any | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents })

  // Pick a valid agent once the list loads
  useEffect(() => {
    if (!agents?.length) return
    const validIds = agents.map((a: any) => a.id)
    if (!agentId || !validIds.includes(agentId)) {
      setAgentId(agents[0].id)
    }
  }, [agents])

  // Persist session whenever key state changes
  useEffect(() => {
    saveSession({ agentId, messages, pendingTrigger, pendingStatus })
  }, [agentId, messages, pendingTrigger, pendingStatus])

  // Poll while waiting for a response
  const { data: pollData } = useQuery({
    queryKey: ['chat-poll', pendingTrigger],
    queryFn: () => api.chatPoll(pendingTrigger!),
    enabled: !!pendingTrigger,
    refetchInterval: 1500,
    refetchIntervalInBackground: true, // keep polling even if tab loses focus
  })

  // Re-fetch checkpoint if we're in awaiting_approval state but lost hitlData
  // (e.g. navigated away and came back)
  useEffect(() => {
    if (pendingTrigger && pendingStatus === 'awaiting_approval' && !hitlData) {
      api.getCheckpoint(pendingTrigger).then(cp => setHitlData(cp)).catch(() => {})
    }
  }, [pendingTrigger, pendingStatus])

  useEffect(() => {
    if (!pollData || !pendingTrigger) return

    const status = pollData.status
    setPendingStatus(status)

    if (status === 'done' && pollData.response) {
      setMessages(prev => [...prev, {
        id: pendingTrigger,
        role: 'agent',
        text: pollData.response!,
        ts: Date.now(),
      }])
      setPendingTrigger(null)
      setHitlData(null)
    } else if (status === 'failed') {
      const errText = pollData.error
        ? `Agent failed: ${pollData.error}`
        : 'Agent failed to respond. Check the backend logs.'
      setMessages(prev => [...prev, {
        id: pendingTrigger,
        role: 'agent',
        text: errText,
        ts: Date.now(),
      }])
      setPendingTrigger(null)
      setHitlData(null)
    } else if (status === 'awaiting_approval' && !hitlData) {
      api.getCheckpoint(pendingTrigger).then(cp => setHitlData(cp)).catch(() => {})
    }
  }, [pollData])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, pendingTrigger, hitlData])

  const send = async (e: FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending || !!pendingTrigger) return

    const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', text, ts: Date.now() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setSending(true)
    setHitlData(null)
    setPendingStatus('queued')

    try {
      const res = await api.chatSend(agentId, text)
      setPendingTrigger(res.trigger_id)
    } catch (err: any) {
      setMessages(prev => [...prev, {
        id: `err-${Date.now()}`,
        role: 'agent',
        text: `Error: ${err.message}`,
        ts: Date.now(),
      }])
    } finally {
      setSending(false)
    }
  }

  const handleHitlAction = async (action: 'approve' | 'reject') => {
    if (!pendingTrigger) return
    try {
      await api.resumeTrigger(pendingTrigger, action)
      setHitlData(null)
      setPendingStatus('running')
    } catch (err: any) {
      setMessages(prev => [...prev, {
        id: `err-${Date.now()}`,
        role: 'agent',
        text: `HITL resume failed: ${err.message}`,
        ts: Date.now(),
      }])
      setPendingTrigger(null)
      setHitlData(null)
    }
  }

  const clearChat = () => {
    setMessages([])
    setPendingTrigger(null)
    setHitlData(null)
    setPendingStatus('queued')
    clearSession()
  }

  const isWaiting = !!pendingTrigger

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06] bg-[#070e1c]/80 backdrop-blur-sm">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Agent Chat</h1>
          <p className="text-xs text-slate-500 mt-0.5">Send messages to agents and get responses in real-time</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-500">Agent:</label>
            <select
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
              disabled={isWaiting}
              className="text-sm bg-white/[0.05] border border-white/[0.08] text-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-cyan-500/40 disabled:opacity-50"
            >
              {agents?.map((a: any) => (
                <option key={a.id} value={a.id} className="bg-slate-800">{a.name ?? a.id}</option>
              )) ?? null}
            </select>
          </div>
          {(messages.length > 0 || pendingTrigger) && (
            <button
              onClick={clearChat}
              title="Clear conversation"
              className="p-1.5 rounded-lg text-slate-600 hover:text-slate-400 hover:bg-white/[0.05] transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && !isWaiting && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="w-16 h-16 rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center">
              <Bot size={28} className="text-slate-700" />
            </div>
            <p className="text-sm text-slate-600">Send a message to start a conversation</p>
          </div>
        )}

        {messages.map(msg => (
          <div key={msg.id} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
              msg.role === 'user'
                ? 'bg-gradient-to-br from-cyan-500 to-indigo-600 shadow-md shadow-cyan-500/20'
                : 'bg-white/[0.08] border border-white/[0.08]'
            }`}>
              {msg.role === 'user'
                ? <User size={14} className="text-white" />
                : <Bot size={14} className="text-slate-400" />
              }
            </div>
            <div className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-gradient-to-br from-cyan-600 to-indigo-700 text-white rounded-tr-sm shadow-md shadow-cyan-500/10 whitespace-pre-wrap'
                : 'bg-slate-800/80 border border-white/[0.08] text-slate-200 rounded-tl-sm'
            }`}>
              {msg.role === 'agent' ? (
                <div className="prose prose-invert prose-sm max-w-none
                  prose-headings:text-slate-100 prose-headings:font-semibold prose-headings:my-2
                  prose-p:text-slate-200 prose-p:my-1.5
                  prose-strong:text-slate-100
                  prose-code:text-cyan-300 prose-code:bg-white/[0.08] prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
                  prose-pre:bg-white/[0.06] prose-pre:border prose-pre:border-white/[0.08] prose-pre:rounded-lg
                  prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5
                  prose-hr:border-white/[0.1]
                  prose-a:text-cyan-400 prose-a:no-underline hover:prose-a:underline
                  prose-blockquote:border-cyan-500/40 prose-blockquote:text-slate-400">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
                </div>
              ) : msg.text}
            </div>
          </div>
        ))}

        {/* HITL approval panel */}
        {pendingTrigger && pendingStatus === 'awaiting_approval' && (
          <div className="flex gap-3">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-amber-500/20 border border-amber-500/30 flex items-center justify-center">
              <AlertTriangle size={14} className="text-amber-400" />
            </div>
            <div className="flex-1 max-w-[80%] bg-amber-950/40 border border-amber-500/30 rounded-2xl rounded-tl-sm px-4 py-3 space-y-3">
              <div>
                <p className="text-sm font-medium text-amber-300">Human approval required</p>
                <p className="text-xs text-amber-500/70 mt-0.5">Review the agent's pending action before it continues</p>
              </div>
              {hitlData?.pending_calls?.length > 0 ? (
                <div className="space-y-2">
                  {hitlData.pending_calls.map((call: any, i: number) => (
                    <div key={i} className="text-xs bg-white/[0.04] border border-white/[0.06] rounded-lg p-2.5 space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-slate-600 uppercase tracking-wider">Tool</span>
                        <span className="text-amber-400 font-mono font-medium">{call.name}</span>
                      </div>
                      <div>
                        <span className="text-[10px] text-slate-600 uppercase tracking-wider">Input</span>
                        <pre className="mt-0.5 text-slate-400 overflow-x-auto text-[11px] whitespace-pre-wrap leading-relaxed">
                          {JSON.stringify(call.input, null, 2)}
                        </pre>
                      </div>
                      {call.result != null && (
                        <div>
                          <span className="text-[10px] text-slate-600 uppercase tracking-wider">Result</span>
                          <pre className="mt-0.5 text-slate-300 overflow-x-auto text-[11px] whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto">
                            {call.result}
                          </pre>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-500 italic">Loading tool details…</p>
              )}
              <div className="flex gap-2 pt-0.5">
                <button
                  onClick={() => handleHitlAction('approve')}
                  className="flex items-center gap-1.5 text-xs bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/40 text-emerald-300 rounded-lg px-3 py-1.5 transition-colors font-medium"
                >
                  <CheckCircle size={13} /> Approve & continue
                </button>
                <button
                  onClick={() => handleHitlAction('reject')}
                  className="flex items-center gap-1.5 text-xs bg-red-500/20 hover:bg-red-500/30 border border-red-500/40 text-red-300 rounded-lg px-3 py-1.5 transition-colors"
                >
                  <XCircle size={13} /> Reject
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Typing / working indicator */}
        {pendingTrigger && pendingStatus !== 'awaiting_approval' && (
          <div className="flex gap-3">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/[0.08] border border-white/[0.08] flex items-center justify-center">
              <Bot size={14} className="text-slate-400" />
            </div>
            <div className="bg-slate-800/80 border border-white/[0.08] rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-2">
              <Loader2 size={14} className="text-cyan-400 animate-spin" />
              <span className="text-sm text-slate-500">
                {pendingStatus === 'running' ? 'Agent is working…' : 'Starting agent…'}
              </span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <form onSubmit={send} className="px-6 py-4 border-t border-white/[0.06] bg-[#070e1c]/80 backdrop-blur-sm">
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            disabled={isWaiting || sending}
            placeholder={isWaiting && pendingStatus === 'awaiting_approval'
              ? 'Waiting for your approval above…'
              : 'Type a message…'}
            className="flex-1 bg-white/[0.04] border border-white/[0.08] text-slate-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500/40 disabled:opacity-50 placeholder:text-slate-600"
          />
          <button
            type="submit"
            disabled={!input.trim() || isWaiting || sending}
            className="bg-cyan-500 hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed text-slate-950 font-semibold rounded-xl px-4 py-2.5 transition-colors shadow-sm shadow-cyan-500/20"
          >
            <Send size={16} />
          </button>
        </div>
      </form>
    </div>
  )
}
