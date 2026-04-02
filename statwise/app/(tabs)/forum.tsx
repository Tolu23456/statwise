import React, { useState, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity,
  ActivityIndicator, KeyboardAvoidingView, Platform, RefreshControl,
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
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const flatListRef = useRef<FlatList>(null);

  const topInset = Platform.OS === 'web' ? 67 : insets.top;

  useEffect(() => {
    loadMessages();

    const channel = supabase
      .channel('forum_messages')
      .on('postgres_changes', {
        event: 'INSERT', schema: 'public', table: 'forum_messages',
      }, payload => {
        const msg = payload.new as ForumMessage;
        setMessages(prev => [...prev, msg]);
        setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
      })
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  async function loadMessages() {
    setLoading(true);
    const { data, error } = await supabase
      .from('forum_messages')
      .select('*, user_profiles(username, display_name, avatar_url, current_tier)')
      .order('created_at', { ascending: true })
      .limit(100);
    if (data) setMessages(data as ForumMessage[]);
    setLoading(false);
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: false }), 200);
  }

  async function sendMessage() {
    if (!input.trim() || !user) return;
    setSending(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const content = input.trim();
    setInput('');
    const { error } = await supabase.from('forum_messages').insert({
      user_id: user.id,
      content,
      created_at: new Date().toISOString(),
    });
    if (error) {
      console.warn('Error sending message:', error);
    }
    setSending(false);
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

    return (
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && (
          <View style={[styles.avatar, { backgroundColor: C.primaryLight }]}>
            <Text style={[styles.avatarText, { color: C.primary }]}>
              {getInitials(msgProfile?.display_name ?? msgProfile?.username)}
            </Text>
          </View>
        )}
        <View style={[styles.bubble, isMe && styles.bubbleMe, {
          backgroundColor: isMe ? C.primary : C.card,
          borderColor: isMe ? C.primary : C.border,
        }]}>
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
          <Text style={[styles.msgText, { color: isMe ? '#fff' : C.text }]}>{item.content}</Text>
          <Text style={[styles.msgTime, { color: isMe ? 'rgba(255,255,255,0.6)' : C.textMuted }]}>
            {formatTime(item.created_at)}
          </Text>
        </View>
      </View>
    );
  };

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

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={C.primary} />
        </View>
      ) : (
        <FlatList
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          contentContainerStyle={styles.list}
          renderItem={renderMessage}
          scrollEnabled={!!messages.length}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="chatbubbles-outline" size={44} color={C.textMuted} />
              <Text style={[styles.emptyTitle, { color: C.text }]}>No messages yet</Text>
              <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>Be the first to start the conversation!</Text>
            </View>
          }
        />
      )}

      <View style={[styles.inputBar, {
        backgroundColor: C.card,
        borderTopColor: C.border,
        paddingBottom: insets.bottom > 0 ? insets.bottom : (Platform.OS === 'web' ? 34 : 12),
      }]}>
        <TextInput
          style={[styles.input, { backgroundColor: C.inputBg, borderColor: C.border, color: C.text }]}
          placeholder="Write a message..."
          placeholderTextColor={C.placeholder}
          value={input}
          onChangeText={setInput}
          multiline
          maxLength={500}
          returnKeyType="send"
          onSubmitEditing={sendMessage}
        />
        <TouchableOpacity
          style={[styles.sendBtn, { backgroundColor: input.trim() ? C.primary : C.border }]}
          onPress={sendMessage}
          disabled={!input.trim() || sending}
        >
          {sending
            ? <ActivityIndicator color="#fff" size="small" />
            : <Ionicons name="send" size={18} color="#fff" />
          }
        </TouchableOpacity>
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
  list: { paddingHorizontal: 12, paddingTop: 8, paddingBottom: 8, gap: 8 },
  msgRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, marginBottom: 4 },
  msgRowMe: { flexDirection: 'row-reverse' },
  avatar: {
    width: 36, height: 36, borderRadius: 18,
    alignItems: 'center', justifyContent: 'center',
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
  msgTime: { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 4, alignSelf: 'flex-end' },
  inputBar: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 10,
    paddingHorizontal: 12, paddingTop: 12, borderTopWidth: 1,
  },
  input: {
    flex: 1, borderRadius: 20, borderWidth: 1,
    paddingHorizontal: 14, paddingVertical: 10,
    fontSize: 15, fontFamily: 'Inter_400Regular',
    maxHeight: 100,
  },
  sendBtn: {
    width: 42, height: 42, borderRadius: 21,
    alignItems: 'center', justifyContent: 'center',
  },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { alignItems: 'center', paddingTop: 80, gap: 12 },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center' },
});
