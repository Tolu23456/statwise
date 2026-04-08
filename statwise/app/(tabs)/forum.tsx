import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity,
  ActivityIndicator, KeyboardAvoidingView, Platform, RefreshControl,
  Alert,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, ForumMessage } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';

export default function ForumScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { user, profile } = useAuth();

  const [messages, setMessages] = useState<ForumMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const flatListRef = useRef<FlatList>(null);

  const topInset = Platform.OS === 'web' ? 0 : insets.top;

  const loadMessages = useCallback(async () => {
    setLoadError(null);
    const { data, error } = await supabase
      .from('forum_messages')
      .select('*, user_profiles(username, display_name, avatar_url, current_tier)')
      .order('created_at', { ascending: true })
      .limit(100);

    if (error) {
      setLoadError(friendlyError(error));
    } else {
      setMessages((data ?? []) as ForumMessage[]);
      setTimeout(() => flatListRef.current?.scrollToEnd({ animated: false }), 200);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    loadMessages().finally(() => setLoading(false));

    const channel = supabase
      .channel('forum_messages')
      .on('postgres_changes', {
        event: 'INSERT', schema: 'public', table: 'forum_messages',
      }, payload => {
        const msg = payload.new as ForumMessage;
        setMessages(prev => {
          const exists = prev.some(m => m.id === msg.id);
          if (exists) return prev;
          return [...prev, msg];
        });
        setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
      })
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, [loadMessages]);

  async function onRefresh() {
    setRefreshing(true);
    await loadMessages();
    setRefreshing(false);
  }

  async function sendMessage() {
    const text = input.trim();
    if (!text || !user) return;
    setSendError(null);
    setSending(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    const optimisticId = `optimistic_${Date.now()}`;
    const optimistic: ForumMessage = {
      id: optimisticId,
      user_id: user.id,
      message: text,
      created_at: new Date().toISOString(),
      user_profiles: {
        username: profile?.username ?? '',
        display_name: profile?.display_name ?? 'You',
        avatar_url: profile?.avatar_url,
        current_tier: profile?.current_tier ?? 'Free Tier',
      },
    };

    setMessages(prev => [...prev, optimistic]);
    setInput('');
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);

    const { error } = await supabase.rpc('send_forum_message', { p_message: text });

    if (error) {
      setMessages(prev => prev.filter(m => m.id !== optimisticId));
      setInput(text);
      const msg = friendlyError(error);
      setSendError(msg);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    }

    setSending(false);
  }

  function friendlyError(error: any): string {
    const msg: string = error?.message ?? error?.toString() ?? 'Unknown error';
    if (msg.includes('row-level security') || msg.includes('42501') || error?.code === '42501') {
      return 'Posting is currently disabled. The forum table needs RLS policies set up in Supabase — check the setup guide below.';
    }
    if (msg.includes('does not exist') || msg.includes('42P01') || error?.code === '42P01') {
      return 'The forum table does not exist yet. Run the setup SQL in your Supabase dashboard.';
    }
    if (msg.includes('network') || msg.includes('fetch') || msg.includes('Failed')) {
      return 'Connection error. Check your internet and try again.';
    }
    return msg;
  }

  function getInitials(name?: string) {
    if (!name) return '?';
    return name.split(' ').map(n => n[0]).join('').toUpperCase().substring(0, 2);
  }

  function formatTime(ts: string) {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  const renderMessage = ({ item }: { item: ForumMessage }) => {
    const isMe = item.user_id === user?.id;
    const msgProfile = item.user_profiles;
    const tierColors = TierBadgeColors[msgProfile?.current_tier ?? 'Free Tier'];
    const isPending = item.id.startsWith('optimistic_');

    return (
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && (
          <View style={[styles.avatar, { backgroundColor: C.primaryLight }]}>
            <Text style={[styles.avatarText, { color: C.primary }]}>
              {getInitials(msgProfile?.display_name ?? msgProfile?.username)}
            </Text>
          </View>
        )}
        <View style={[
          styles.bubble,
          isMe && styles.bubbleMe,
          {
            backgroundColor: isMe ? C.primary : C.card,
            borderColor: isMe ? C.primary : C.border,
            opacity: isPending ? 0.65 : 1,
          },
        ]}>
          {!isMe && (
            <View style={styles.nameRow}>
              <Text style={[styles.senderName, { color: C.text }]}>
                {msgProfile?.display_name ?? msgProfile?.username ?? 'User'}
              </Text>
              {msgProfile?.current_tier && msgProfile.current_tier !== 'Free Tier' && (
                <View style={[styles.tierPill, { backgroundColor: tierColors.bg }]}>
                  <Text style={[styles.tierPillText, { color: tierColors.text }]}>
                    {msgProfile.current_tier.replace(' Tier', '')}
                  </Text>
                </View>
              )}
            </View>
          )}
          <Text style={[styles.msgText, { color: isMe ? '#fff' : C.text }]}>{item.message}</Text>
          <View style={styles.msgMeta}>
            {isPending && <Ionicons name="time-outline" size={11} color={isMe ? 'rgba(255,255,255,0.5)' : C.textMuted} />}
            <Text style={[styles.msgTime, { color: isMe ? 'rgba(255,255,255,0.6)' : C.textMuted }]}>
              {isPending ? 'Sending…' : formatTime(item.created_at)}
            </Text>
          </View>
        </View>
      </View>
    );
  };

  const showSetupGuide = (loadError ?? sendError ?? '').toLowerCase().includes('rls') ||
    (loadError ?? sendError ?? '').toLowerCase().includes('does not exist') ||
    (loadError ?? sendError ?? '').toLowerCase().includes('setup');

  return (
    <KeyboardAvoidingView
      style={[styles.container, { backgroundColor: C.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={0}
    >
      <View style={[styles.header, { paddingTop: topInset + 8 }]}>
        <Text style={[styles.title, { color: C.text }]}>Community</Text>
        <View style={[styles.onlinePill, { backgroundColor: C.successLight }]}>
          <View style={[styles.dot, { backgroundColor: C.success }]} />
          <Text style={[styles.onlineText, { color: C.success }]}>Live</Text>
        </View>
      </View>

      {loadError && (
        <TouchableOpacity
          style={[styles.errorBanner, { backgroundColor: C.dangerLight, borderColor: C.danger }]}
          onPress={onRefresh}
          activeOpacity={0.8}
        >
          <Ionicons name="warning-outline" size={15} color={C.danger} />
          <Text style={[styles.errorBannerText, { color: C.danger }]}>{loadError}</Text>
          <Ionicons name="refresh-outline" size={15} color={C.danger} />
        </TouchableOpacity>
      )}

      {showSetupGuide && (
        <View style={[styles.setupGuide, { backgroundColor: C.warningLight, borderColor: C.warning }]}>
          <Ionicons name="construct-outline" size={16} color={C.warning} />
          <View style={{ flex: 1 }}>
            <Text style={[styles.setupTitle, { color: C.warning }]}>One-time setup required</Text>
            <Text style={[styles.setupDesc, { color: C.textSecondary }]}>
              Run the SQL in{' '}
              <Text style={{ fontFamily: 'Inter_600SemiBold' }}>statwise/supabase-forum-setup.sql</Text>
              {' '}in your Supabase dashboard → SQL Editor to enable forum posting.
            </Text>
          </View>
        </View>
      )}

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={C.primary} />
          <Text style={[styles.loadingText, { color: C.textSecondary }]}>Loading messages…</Text>
        </View>
      ) : (
        <FlatList
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          contentContainerStyle={[
            styles.list,
            messages.length === 0 && styles.listEmpty,
          ]}
          renderItem={renderMessage}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={C.primary}
              colors={[C.primary]}
            />
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <View style={[styles.emptyIconBg, { backgroundColor: C.primaryLight }]}>
                <Ionicons name="chatbubbles-outline" size={36} color={C.primary} />
              </View>
              <Text style={[styles.emptyTitle, { color: C.text }]}>No messages yet</Text>
              <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>
                Be the first to start the conversation! Type a message below and press send.
              </Text>
            </View>
          }
        />
      )}

      {sendError && !showSetupGuide && (
        <View style={[styles.sendErrorBar, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
          <Ionicons name="alert-circle-outline" size={14} color={C.danger} />
          <Text style={[styles.sendErrorText, { color: C.danger }]} numberOfLines={2}>{sendError}</Text>
          <TouchableOpacity onPress={() => setSendError(null)}>
            <Ionicons name="close" size={16} color={C.danger} />
          </TouchableOpacity>
        </View>
      )}

      <View style={[styles.inputBar, {
        backgroundColor: C.card,
        borderTopColor: C.border,
        paddingBottom: insets.bottom > 0 ? insets.bottom : (Platform.OS === 'web' ? 16 : 12),
      }]}>
        {!user ? (
          <View style={[styles.loginPrompt, { backgroundColor: C.inputBg, borderColor: C.border }]}>
            <Ionicons name="lock-closed-outline" size={16} color={C.textSecondary} />
            <Text style={[styles.loginPromptText, { color: C.textSecondary }]}>
              Sign in to join the conversation
            </Text>
          </View>
        ) : (
          <>
            <TextInput
              style={[styles.input, { backgroundColor: C.inputBg, borderColor: sendError ? C.danger : C.border, color: C.text }]}
              placeholder="Write a message…"
              placeholderTextColor={C.placeholder}
              value={input}
              onChangeText={v => { setInput(v); if (sendError) setSendError(null); }}
              multiline
              maxLength={500}
              returnKeyType="send"
              blurOnSubmit={false}
              onSubmitEditing={sendMessage}
            />
            <TouchableOpacity
              style={[
                styles.sendBtn,
                { backgroundColor: input.trim() && !sending ? C.primary : C.border },
              ]}
              onPress={sendMessage}
              disabled={!input.trim() || sending}
              activeOpacity={0.85}
            >
              {sending
                ? <ActivityIndicator color="#fff" size="small" />
                : <Ionicons name="send" size={18} color="#fff" />
              }
            </TouchableOpacity>
          </>
        )}
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 16, paddingBottom: 10,
  },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', flex: 1 },
  onlinePill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  onlineText: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  errorBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    marginHorizontal: 12, borderRadius: 10, borderWidth: 1, padding: 10, marginBottom: 4,
  },
  errorBannerText: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  setupGuide: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    marginHorizontal: 12, borderRadius: 12, borderWidth: 1, padding: 12, marginBottom: 4,
  },
  setupTitle: { fontSize: 13, fontFamily: 'Inter_600SemiBold', marginBottom: 3 },
  setupDesc: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  list: { paddingHorizontal: 12, paddingTop: 8, paddingBottom: 8, gap: 4 },
  listEmpty: { flexGrow: 1, justifyContent: 'center' },
  msgRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, marginBottom: 6 },
  msgRowMe: { flexDirection: 'row-reverse' },
  avatar: {
    width: 36, height: 36, borderRadius: 18,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  avatarText: { fontSize: 13, fontFamily: 'Inter_700Bold' },
  bubble: {
    maxWidth: '75%', borderRadius: 16, borderWidth: 1, padding: 12,
    borderBottomLeftRadius: 4,
  },
  bubbleMe: { borderBottomLeftRadius: 16, borderBottomRightRadius: 4 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 },
  senderName: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  tierPill: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 10 },
  tierPillText: { fontSize: 10, fontFamily: 'Inter_600SemiBold' },
  msgText: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 20 },
  msgMeta: { flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 4, alignSelf: 'flex-end' },
  msgTime: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  sendErrorBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginHorizontal: 12, borderRadius: 10, borderWidth: 1, padding: 8, marginBottom: 4,
  },
  sendErrorText: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular' },
  inputBar: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 10,
    paddingHorizontal: 12, paddingTop: 10, borderTopWidth: 1,
  },
  input: {
    flex: 1, borderRadius: 20, borderWidth: 1,
    paddingHorizontal: 14, paddingVertical: 10,
    fontSize: 15, fontFamily: 'Inter_400Regular', maxHeight: 100,
  },
  sendBtn: {
    width: 42, height: 42, borderRadius: 21,
    alignItems: 'center', justifyContent: 'center',
  },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  loadingText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  empty: { alignItems: 'center', paddingHorizontal: 32, gap: 12 },
  emptyIconBg: { width: 80, height: 80, borderRadius: 40, alignItems: 'center', justifyContent: 'center' },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 20 },
  loginPrompt: {
    flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 20, borderWidth: 1, paddingHorizontal: 14, paddingVertical: 10,
  },
  loginPromptText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
});
