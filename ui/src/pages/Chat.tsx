/**
 * Chat page — send a message to an agent and poll for the response.
 * Available to all authenticated users (end-user and above).
 */
import { useState, useRef, useEffect, FormEvent } from 'react'
import { Send, Bot, User, Loader2 } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface Message {
  id: string
  role: 'user' | 'agent'
  text: string
  ts: number
}

export default function Chat() {
  const [agentId, setAgentId] = useState('telegram-agent')
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [pendingTrigger, setPendingTrigger] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents })

  const { data: pollData } = useQuery({
    queryKey: ['chat-poll', pendingTrigger],
    queryFn: () => api.chatPoll(pendingTrigger!),
    enabled: !!pendingTrigger,
    refetchInterval: 1500,
  })

  useEffect(() => {
    if (!pollData || !pendingTrigger) return
    if (pollData.status === 'done' && pollData.response) {
      setMessages(prev => [...prev, {
        id: pendingTrigger,
        role: 'agent',
        text: pollData.response!,
        ts: Date.now(),
      }])
      setPendingTrigger(null)
    } else if (pollData.status === 'failed') {
      setMessages(prev => [...prev, {
        id: pendingTrigger,
        role: 'agent',
        text: '⚠️ Agent failed to respond. Check logs.',
        ts: Date.now(),
      }])
      setPendingTrigger(null)
    }
  }, [pollData, pendingTrigger])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, pendingTrigger])

  const send = async (e: FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', text, ts: Date.now() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setSending(true)

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

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06] bg-[#070e1c]/80 backdrop-blur-sm">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Agent Chat</h1>
          <p className="text-xs text-slate-500 mt-0.5">Send messages to agents and get responses in real-time</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-500">Agent:</label>
          <select
            value={agentId}
            onChange={e => setAgentId(e.target.value)}
            className="text-sm bg-white/[0.05] border border-white/[0.08] text-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-cyan-500/40"
          >
            {agents?.map((a: any) => (
              <option key={a.id} value={a.id} className="bg-slate-800">{a.name ?? a.id}</option>
            )) ?? <option value="telegram-agent" className="bg-slate-800">telegram-agent</option>}
          </select>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="w-16 h-16 rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center">
              <Bot size={28} className="text-slate-700" />
            </div>
            <p className="text-sm text-slate-600">Send a message to start a conversation</p>
          </div>
        )}

        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
          >
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
            <div className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed ${
              msg.role === 'user'
                ? 'bg-gradient-to-br from-cyan-600 to-indigo-700 text-white rounded-tr-sm shadow-md shadow-cyan-500/10'
                : 'bg-slate-800/80 border border-white/[0.08] text-slate-200 rounded-tl-sm'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}

        {/* Typing indicator */}
        {pendingTrigger && (
          <div className="flex gap-3">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/[0.08] border border-white/[0.08] flex items-center justify-center">
              <Bot size={14} className="text-slate-400" />
            </div>
            <div className="bg-slate-800/80 border border-white/[0.08] rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-2">
              <Loader2 size={14} className="text-cyan-400 animate-spin" />
              <span className="text-sm text-slate-500">Agent is thinking…</span>
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
            disabled={!!pendingTrigger || sending}
            placeholder="Type a message…"
            className="flex-1 bg-white/[0.04] border border-white/[0.08] text-slate-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500/40 disabled:opacity-50 placeholder:text-slate-600"
          />
          <button
            type="submit"
            disabled={!input.trim() || !!pendingTrigger || sending}
            className="bg-cyan-500 hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed text-slate-950 font-semibold rounded-xl px-4 py-2.5 transition-colors shadow-sm shadow-cyan-500/20"
          >
            <Send size={16} />
          </button>
        </div>
      </form>
    </div>
  )
}
