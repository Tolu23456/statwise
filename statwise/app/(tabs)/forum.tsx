import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity,
  ActivityIndicator, KeyboardAvoidingView, Platform, RefreshControl,
  Alert, Image, Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, ForumMessage } from '@/lib/supabase';
import {
  getForumMessages,
  refreshForumMessages,
  removeOptimisticForumMessage,
  subscribeForumMessages,
  upsertForumMessages,
} from '@/lib/forumFeed';
import { buildForumContent, replyPreview, tokenizeMentions } from '@/lib/forumFeedCore';
import { useFocusEffect } from 'expo-router';
import { AnimatedEntrance } from '@/components/AnimatedEntrance';
import { SkeletonChatRow } from '@/components/Skeletons';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';
import { formatChatTime, formatDayLabel, dayOffset } from '@/lib/format';
import { LIMITS, exceedsLimit, sanitizeUserText } from '@/lib/validation';
import { checkRateLimit, RATE_RULES, rateLimitMessage } from '@/lib/rateLimit';

const PREMIUM_TIERS = ['Premium Tier', 'VIP Tier', 'VVIP Tier'];

type ParsedMessage = { text?: string; image?: string; video?: string };

function parseMessage(raw: string): ParsedMessage {
  try {
    const p = JSON.parse(raw);
    if (p && typeof p === 'object' && ('text' in p || 'image' in p || 'video' in p)) {
      // Plan 4.3 — only https media URLs are rendered (an attacker-controlled
      // message could otherwise point the image/video elements anywhere,
      // including javascript:/data: payloads or plain http trackers).
      if (p.image !== undefined && !/^https:\/\/\S+$/.test(String(p.image))) delete p.image;
      if (p.video !== undefined && !/^https:\/\/\S+$/.test(String(p.video))) delete p.video;
      if (p.text !== undefined) p.text = sanitizeUserText(String(p.text));
      return p;
    }
  } catch {}
  return { text: sanitizeUserText(raw) };
}

function friendlyError(error: any): string {
  const msg: string = error?.message ?? error?.toString() ?? 'Unknown error';
  if (msg.includes('row-level security') || msg.includes('42501') || error?.code === '42501') {
    return 'Permission denied (RLS). Please run the SQL fix in your Supabase dashboard.';
  }
  if (msg.includes('send_forum_message') || msg.includes('function') || msg.includes('42883')) {
    // BEFORE the generic "does not exist" branch: a missing function's
    // message ("function ... does not exist") would otherwise be
    // misreported as a missing table. Covers the pre-migration case of
    // passing p_reply_to to the old 1-arg function too.
    return 'Forum function missing or outdated. Run supabase-pending-migrations.sql in your Supabase SQL Editor.';
  }
  if (msg.includes('does not exist') || msg.includes('42P01') || error?.code === '42P01') {
    return 'Forum table missing. Run the setup SQL in your Supabase dashboard.';
  }
  if (msg.includes('not-null') || msg.includes('23502')) {
    return 'Profile issue. Run supabase-forum-fix.sql to fix this.';
  }
  if (msg.includes('network') || msg.includes('fetch') || msg.includes('Failed')) {
    return 'Connection error. Check your internet and try again.';
  }
  return msg.length > 120 ? msg.slice(0, 117) + '...' : msg;
}

export default function ForumScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { user, profile } = useAuth();

  // Feed rows come from the app-wide store (lib/forumFeed.ts): hydrated
  // from AsyncStorage and refreshed in the BACKGROUND by the root-layout
  // watcher — this screen never owns a fetch loop or a realtime channel.
  const [messages, setMessages] = useState<ForumMessage[]>(() => getForumMessages());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [pendingImage, setPendingImage] = useState<string | null>(null);
  const [pendingVideo, setPendingVideo] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [mediaUploading, setMediaUploading] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // Reply target (tap a bubble → quote it). Cleared on send/cancel; the
  // quoted id rides the RPC as p_reply_to and the optimistic bubble.
  const [replyTo, setReplyTo] = useState<ForumMessage | null>(null);
  // Replays the message-entrance animation on every visit to this tab —
  // tab screens stay mounted, so mount-only entering plays just once.
  // Initial focus is skipped (first mount already animates).
  const firstFocus = useRef(true);
  const [visitKey, setVisitKey] = useState(0);
  useFocusEffect(
    useCallback(() => {
      if (firstFocus.current) {
        firstFocus.current = false;
        return;
      }
      setVisitKey(k => k + 1);
    }, []),
  );
  const flatListRef = useRef<FlatList>(null);
  const inputRef = useRef<TextInput>(null);

  const topInset = Platform.OS === 'web' ? 0 : insets.top;
  const isPremium = PREMIUM_TIERS.includes(profile?.current_tier ?? '');

  useEffect(() => {
    // Mount = subscribe + one freshness pass (shared/deduped with the
    // watcher's own refresh). No channel, no interval to clean up here.
    const unsubscribe = subscribeForumMessages(setMessages);
    let alive = true;
    (async () => {
      const res = await refreshForumMessages();
      if (!alive) return;
      if (!res.ok && res.error) setLoadError(friendlyError(res.error));
      setLoading(false);
    })();
    return () => {
      alive = false;
      unsubscribe();
    };
  }, []);

  async function onRefresh() {
    setRefreshing(true);
    const res = await refreshForumMessages();
    if (!res.ok && res.error) setLoadError(friendlyError(res.error));
    else setLoadError(null);
    setTimeout(() => setRefreshing(false), 300);
  }

  const doSend = useCallback(async (content: string, reply: ForumMessage | null = null): Promise<boolean> => {
    if (!content.trim() || !user) return false;
    setSendError(null);
    setSending(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    const optimisticId = `optimistic_${Date.now()}`;
    const optimistic: ForumMessage = {
      id: optimisticId,
      user_id: user.id,
      message: content,
      created_at: new Date().toISOString(),
      reply_to: reply?.id ?? null,
      user_profiles: {
        username: profile?.username ?? profile?.display_name ?? 'You',
        display_name: profile?.display_name ?? profile?.username ?? 'You',
        avatar_url: profile?.avatar_url,
        current_tier: profile?.current_tier ?? 'Free Tier',
      },
    };

    // Store-owned bubble: the merge reconciles it against the confirmed row
    // (same user + same message) when realtime/the next refresh delivers it,
    // and the 5-minute TTL clears it if the send truly never landed.
    upsertForumMessages([optimistic]);
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);

    // p_reply_to rides along only when set: the pre-migration 1-arg
    // function would reject an unexpected named arg, so omit it entirely.
    const { error } = await supabase.rpc('send_forum_message', {
      p_message: content,
      ...(reply?.id ? { p_reply_to: reply.id } : {}),
    });

    if (error) {
      removeOptimisticForumMessage(optimisticId);
      setSendError(friendlyError(error));
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      setSending(false);
      return false;
    }

    // Belt-and-braces: pull the confirmed row now so the bubble flips from
    // pending to real without waiting for the watcher's next tick.
    void refreshForumMessages();
    setSending(false);
    return true;
  }, [user, profile]);

  async function sendMessage() {
    const raw = input.trim();
    const hasAttachment = Boolean(pendingImage || pendingVideo);
    if ((!raw && !hasAttachment) || sending || mediaUploading) return;
    // Plan 4.3 — explicit length guard instead of silent truncation.
    if (exceedsLimit(raw, LIMITS.messageMax)) {
      setSendError(`Messages are limited to ${LIMITS.messageMax} characters.`);
      return;
    }
    // Plan 4.3 — client half of the limit; send_forum_message enforces the
    // same 6 / 60s window server-side for anyone who skips this screen.
    const verdict = checkRateLimit('forum:send', RATE_RULES.forumSend);
    if (!verdict.allowed) {
      setSendError(rateLimitMessage(verdict));
      return;
    }
    const text = sanitizeUserText(raw, LIMITS.messageMax);
    // Envelope + cap check BEFORE clearing the composer: the server
    // left(..., 1000) would corrupt an over-long JSON into raw source.
    const { content, overflow } = buildForumContent(text, pendingImage, pendingVideo);
    if (overflow > 0) {
      setSendError(`Message is ${overflow} characters too long — shorten the text or remove an attachment.`);
      return;
    }
    if (!content) return;

    const restoreText = text;
    const restoreImage = pendingImage;
    const restoreVideo = pendingVideo;
    const replyTarget = replyTo; // capture before the composer resets
    setInput('');
    setPendingImage(null);
    setPendingVideo(null);
    const ok = await doSend(content, replyTarget);
    if (!ok) {
      // Nothing the user composed is lost to a failed send — restore it.
      setInput(prev => (prev ? prev : restoreText));
      setPendingImage(prev => prev ?? restoreImage);
      setPendingVideo(prev => prev ?? restoreVideo);
    } else {
      setReplyTo(null); // sent — drop the quote chip
    }
  }

  async function pickAndUploadMedia(type: 'image' | 'video') {
    if (!isPremium) {
      Alert.alert(
        'Premium Feature',
        `Sending ${type}s is available for Premium, VIP, and VVIP members. Upgrade your plan to unlock this feature.`,
        [{ text: 'OK' }],
      );
      return;
    }

    let pickerResult: ImagePicker.ImagePickerResult;
    try {
      pickerResult = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: type === 'image'
          ? ImagePicker.MediaTypeOptions.Images
          : ImagePicker.MediaTypeOptions.Videos,
        quality: 0.8,
        allowsEditing: false,
      });
    } catch {
      setSendError(
        Platform.OS === 'web'
          ? 'This browser blocked the picker. Try a different browser or attach an image instead.'
          : 'Could not open the media picker.',
      );
      return;
    }

    if (pickerResult.canceled || !pickerResult.assets?.[0]) return;

    const asset = pickerResult.assets[0];
    const ext = (asset.uri.split('.').pop() ?? (type === 'image' ? 'jpg' : 'mp4')).toLowerCase();
    const path = `forum/${user!.id}/${Date.now()}.${ext}`;
    const mimeType = asset.mimeType ?? (type === 'image' ? `image/${ext}` : `video/${ext}`);

    setMediaUploading(true);
    setSendError(null);

    try {
      const resp = await fetch(asset.uri);
      const blob = await resp.blob();

      const { error: uploadError } = await supabase.storage
        .from('forum-media')
        .upload(path, blob, { contentType: mimeType });

      if (uploadError) throw uploadError;

      const { data: { publicUrl } } = supabase.storage.from('forum-media').getPublicUrl(path);

      // Attachment is staged, not sent: pick an image AND a video, add a
      // caption, send everything as ONE message.
      if (type === 'image') setPendingImage(publicUrl);
      else setPendingVideo(publicUrl);
    } catch (err: any) {
      setSendError(
        'Upload failed. Make sure a "forum-media" storage bucket exists in your Supabase dashboard (Storage → New bucket → "forum-media", set to Public).',
      );
    } finally {
      setMediaUploading(false);
    }
  }

  // Quoted parent for the reply bar: look it up in the loaded window. If
  // the parent scrolled out of the 100-row window the quote degrades to
  // a generic label rather than showing nothing (the SQL side already
  // refused to persist a reply to a deleted message).
  const parentOf = (replyId?: string | null): ForumMessage | null =>
    replyId ? messages.find(m => m.id === replyId) ?? null : null;

  const startReply = (item: ForumMessage) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setReplyTo(item);
    inputRef.current?.focus();
  };

  const renderMentionText = (text: string, isMe: boolean) => (
    // Mention tokens (@handle) render highlighted; everything else is
    // the ordinary bubble text. Mention regex mirrors the SQL trigger.
    // C.text keeps received messages readable in BOTH themes — msgText had
    // no color, so RN fell back to black (invisible on the dark bubble).
    <Text style={[styles.msgText, { color: C.text }, isMe && { color: '#fff' }]}>
      {tokenizeMentions(text).map((seg, i) =>
        seg.mention ? (
          <Text
            key={i}
            style={{
              color: isMe ? '#ffe8a3' : C.primary,
              fontFamily: 'Inter_600SemiBold',
            }}
          >
            {seg.text}
          </Text>
        ) : (
          <Text key={i}>{seg.text}</Text>
        ),
      )}
    </Text>
  );

  const renderItem = ({ item, index }: { item: ForumMessage; index: number }) => {
    const isMe = user?.id === item.user_id;
    const prev = messages[index - 1];
    const showHeader = !prev || prev.user_id !== item.user_id;
    const parsed = parseMessage(item.message);
    const profileData = (item as any).user_profiles || {};
    const displayName = profileData.display_name || profileData.username || item.username || 'User';
    const avatarUrl = profileData.avatar_url;
    const tier = profileData.current_tier || 'Free Tier';
    const isPremiumUser = PREMIUM_TIERS.includes(tier);

    // Day divider between messages from different calendar days
    const prevDay = prev ? dayOffset(prev.created_at) : null;
    const thisDay = dayOffset(item.created_at);
    const showDayDivider = prevDay === null || prevDay !== thisDay;

    return (
      <AnimatedEntrance index={index}>
      <View>
      {showDayDivider && (
        <View style={styles.dayDivider}>
          <View style={[styles.dayDividerLine, { backgroundColor: C.border }]} />
          <Text style={[styles.dayDividerText, { color: C.textMuted }]}>
            {/* Older than Yesterday: show the actual date (formatChatTime's
                first token used to leak just the day number, "25"). */}
            {formatDayLabel(item.created_at)}
          </Text>
          <View style={[styles.dayDividerLine, { backgroundColor: C.border }]} />
        </View>
      )}
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && showHeader && (
          <View style={[styles.avatar, { backgroundColor: C.primaryLight }]}>
            {avatarUrl ? (
              <Image source={{ uri: avatarUrl }} style={styles.avatarImg} />
            ) : (
              <Text style={[styles.avatarText, { color: C.primary }]}>
                {displayName.slice(0, 2).toUpperCase()}
              </Text>
            )}
          </View>
        )}
        {!isMe && !showHeader && <View style={styles.avatarSpacer} />}
        <View style={[styles.bubbleCol, isMe && styles.bubbleColMe]}>
          {!isMe && showHeader && (
            <View style={styles.senderRow}>
              <Text style={[styles.senderName, { color: C.text }]} numberOfLines={1}>
                {displayName}
              </Text>
              {isPremiumUser && (
                <View style={[styles.tierPill, { backgroundColor: TierBadgeColors[tier]?.bg ?? C.primaryLight }]}>
                  <Text style={[styles.tierPillText, { color: TierBadgeColors[tier]?.text ?? C.primary }]}>
                    {tier === 'VVIP Tier' ? 'VVIP' : tier === 'VIP Tier' ? 'VIP' : 'PREM'}
                  </Text>
                </View>
              )}
            </View>
          )}
          <View style={[
            styles.bubble,
            isMe ? [styles.bubbleMe, { backgroundColor: C.primary }] : [styles.bubbleOther, { backgroundColor: C.card, borderColor: C.border }],
          ]}>
            {item.reply_to ? (
              (() => {
                const parent = parentOf(item.reply_to);
                const parentName =
                  parent?.user_profiles?.display_name ||
                  parent?.user_profiles?.username ||
                  parent?.username ||
                  'a message';
                return (
                  <View style={[styles.quoteBar, { borderLeftColor: isMe ? 'rgba(255,255,255,0.6)' : C.primary }]}>
                    <Ionicons name="arrow-undo" size={11} color={isMe ? 'rgba(255,255,255,0.85)' : C.textMuted} />
                    <Text
                      numberOfLines={1}
                      style={[styles.quoteText, { color: isMe ? 'rgba(255,255,255,0.85)' : C.textMuted }]}
                    >
                      {parentName}: {parent ? replyPreview(parent.message, 48) : 'original message'}
                    </Text>
                  </View>
                );
              })()
            ) : null}
            {parsed.image && (
              <Image source={{ uri: parsed.image }} style={styles.mediaImage} resizeMode="cover" />
            )}
            {parsed.video && Platform.OS === 'web' &&
              // Raw DOM <video> (React web renderer): real playback with
              // controls, zero new dependencies. RN's style keys map 1:1.
              React.createElement('video' as any, {
                src: parsed.video,
                controls: true,
                preload: 'metadata',
                style: {
                  width: 220, height: 140, borderRadius: 12, marginBottom: 6,
                  background: '#000', display: 'block',
                },
              })}
            {parsed.video && Platform.OS !== 'web' && (
              <TouchableOpacity
                style={[styles.mediaPlaceholder, { backgroundColor: C.inputBg }]}
                onPress={() => Linking.openURL(parsed.video!).catch(() => {})}
                activeOpacity={0.8}
              >
                <Ionicons name="play-circle" size={40} color="#fff" />
                <Text style={[styles.mediaPlayText, { color: C.textSecondary }]}>Play video</Text>
              </TouchableOpacity>
            )}
            {parsed.text ? renderMentionText(parsed.text, isMe) : null}
            <View style={styles.msgFooter}>
              <Text style={[styles.timeText, { color: isMe ? 'rgba(255,255,255,0.7)' : C.textMuted }]}>
                {formatChatTime(item.created_at)}
              </Text>
              <TouchableOpacity
                onPress={() => startReply(item)}
                hitSlop={10}
                style={styles.replyBtn}
                accessibilityLabel="Reply to message"
              >
                <Ionicons name="return-down-forward" size={13} color={isMe ? 'rgba(255,255,255,0.7)' : C.textMuted} />
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </View>
      </View>
      </AnimatedEntrance>
    );
  };

  const isBusy = sending || mediaUploading;
  const hasAttachment = Boolean(pendingImage || pendingVideo);
  const canSend = Boolean(input.trim()) || hasAttachment;

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

      {loading && messages.length === 0 ? (
        <View style={styles.listContent}>
          {[0, 1, 2, 3].map(i => (
            <SkeletonChatRow key={i} />
          ))}
        </View>
      ) : (
        <FlatList
          key={`forum-${visitKey}`}
          ref={flatListRef}
          data={messages}
          renderItem={renderItem}
          keyExtractor={item => item.id}
          contentContainerStyle={[styles.listContent, { paddingBottom: 12 }]}
          onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: false })}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} colors={[C.primary]} />}
          ListEmptyComponent={
            <View style={styles.empty}>
              <View style={[styles.emptyIconBg, { backgroundColor: C.primaryLight }]}>
                <Ionicons name="chatbubbles-outline" size={36} color={C.primary} />
              </View>
              <Text style={[styles.emptyTitle, { color: C.text }]}>No messages yet</Text>
              <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>
                Be the first to start the conversation!
              </Text>
            </View>
          }
        />
      )}

      <View style={[styles.inputBar, {
        paddingBottom: (Platform.OS === 'web' ? 16 : Math.max(insets.bottom, 10)),
        backgroundColor: C.background,
        borderTopColor: C.border,
      }]}>
        {sendError && (
          <View style={[styles.sendErrorBar, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
            <Ionicons name="alert-circle-outline" size={14} color={C.danger} />
            <Text style={[styles.sendErrorText, { color: C.danger }]} numberOfLines={3}>{sendError}</Text>
            <TouchableOpacity onPress={() => setSendError(null)}>
              <Ionicons name="close" size={14} color={C.danger} />
            </TouchableOpacity>
          </View>
        )}

        {!user ? (
          <View style={[styles.loginPrompt, { backgroundColor: C.inputBg, borderColor: C.border }]}>
            <Ionicons name="lock-closed-outline" size={16} color={C.textSecondary} />
            <Text style={[styles.loginPromptText, { color: C.textSecondary }]}>
              Sign in to join the conversation
            </Text>
          </View>
        ) : (
          <>
            {replyTo && (
              <View style={[styles.replyChip, { backgroundColor: C.inputBg, borderColor: C.primary }]}>
                <Ionicons name="return-down-forward" size={14} color={C.primary} />
                <View style={{ flex: 1 }}>
                  <Text style={[styles.replyChipName, { color: C.primary }]} numberOfLines={1}>
                    Replying to{' '}
                    {replyTo.user_profiles?.display_name ||
                      replyTo.user_profiles?.username ||
                      replyTo.username ||
                      'a message'}
                  </Text>
                  <Text style={[styles.replyChipPreview, { color: C.textMuted }]} numberOfLines={1}>
                    {replyPreview(replyTo.message, 60)}
                  </Text>
                </View>
                <TouchableOpacity onPress={() => setReplyTo(null)} hitSlop={10} accessibilityLabel="Cancel reply">
                  <Ionicons name="close-circle" size={18} color={C.textMuted} />
                </TouchableOpacity>
              </View>
            )}
            {(pendingImage || pendingVideo) && (
              <View style={styles.attachRow}>
                {pendingImage && (
                  <View style={[styles.attachChip, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                    <Ionicons name="image-outline" size={14} color={C.primary} />
                    <Text style={[styles.attachText, { color: C.textSecondary }]}>Image</Text>
                    <TouchableOpacity onPress={() => setPendingImage(null)} hitSlop={8}>
                      <Ionicons name="close-circle" size={16} color={C.textMuted} />
                    </TouchableOpacity>
                  </View>
                )}
                {pendingVideo && (
                  <View style={[styles.attachChip, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                    <Ionicons name="videocam-outline" size={14} color={C.primary} />
                    <Text style={[styles.attachText, { color: C.textSecondary }]}>Video</Text>
                    <TouchableOpacity onPress={() => setPendingVideo(null)} hitSlop={8}>
                      <Ionicons name="close-circle" size={16} color={C.textMuted} />
                    </TouchableOpacity>
                  </View>
                )}
                {mediaUploading && <ActivityIndicator size="small" color={C.primary} />}
              </View>
            )}
          <View style={styles.inputRow}>
            <View style={styles.mediaIcons}>
              <TouchableOpacity style={styles.mediaBtn} onPress={() => pickAndUploadMedia('image')} activeOpacity={0.7}>
                <Ionicons name="image-outline" size={22} color={isPremium ? C.primary : C.textMuted} />
                {!isPremium && <View style={[styles.lockBadge, { backgroundColor: C.textMuted }]}><Ionicons name="lock-closed" size={7} color="#fff" /></View>}
              </TouchableOpacity>
              <TouchableOpacity style={styles.mediaBtn} onPress={() => pickAndUploadMedia('video')} activeOpacity={0.7}>
                <Ionicons name="videocam-outline" size={22} color={isPremium ? C.primary : C.textMuted} />
                {!isPremium && <View style={[styles.lockBadge, { backgroundColor: C.textMuted }]}><Ionicons name="lock-closed" size={7} color="#fff" /></View>}
              </TouchableOpacity>
            </View>

            <View style={[styles.inputContainer, {
              backgroundColor: C.inputBg,
              borderColor: sendError ? C.danger : C.border,
              alignItems: 'center',
              paddingRight: 6,
            }]}>
              <TextInput
                ref={inputRef}
                style={[
                  styles.textInput,
                  { color: C.text },
                  Platform.OS === 'web' ? ({ outlineStyle: 'none', boxShadow: 'none', borderWidth: 0 } as any) : null,
                ]}
                placeholder="Message…"
                placeholderTextColor={C.placeholder}
                value={input}
                onChangeText={v => { setInput(v); if (sendError) setSendError(null); }}
                multiline={Platform.OS !== 'web'}
                maxLength={1000}
                returnKeyType="send"
                blurOnSubmit={false}
                onSubmitEditing={Platform.OS === 'web' ? sendMessage : undefined}
                underlineColorAndroid="transparent"
                selectionColor={C.primary}
                textAlignVertical="center"
              />

              {(mediaUploading) ? (
                <View style={[styles.sendBtn, { backgroundColor: C.primary }]}>
                  <ActivityIndicator color="#fff" size="small" />
                </View>
              ) : (
                <TouchableOpacity
                  style={[
                    styles.sendBtn,
                    { backgroundColor: canSend && !isBusy ? C.primary : C.border },
                  ]}
                  onPress={sendMessage}
                  disabled={!canSend || isBusy}
                  activeOpacity={0.85}
                >
                  <Ionicons name="arrow-up" size={16} color={canSend && !isBusy ? '#fff' : C.textMuted} />
                </TouchableOpacity>
              )}
            </View>

            {input.length > 800 && (
              <Text style={[styles.charCount, { color: input.length > 950 ? C.danger : C.textMuted }]}>
                {input.length}/1000
              </Text>
            )}
          </View>
          </>
        )}
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingBottom: 10 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', flex: 1 },
  onlinePill: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 20 },
  dot: { width: 6, height: 6, borderRadius: 3 },
  onlineText: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
  errorBanner: { flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 16, marginBottom: 10, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1 },
  errorBannerText: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  loadingText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  listContent: { paddingHorizontal: 16, paddingTop: 8, flexGrow: 1 },
  dayDivider: { flexDirection: 'row', alignItems: 'center', gap: 10, marginVertical: 12 },
  dayDividerLine: { flex: 1, height: 1 },
  dayDividerText: { fontSize: 11, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.6 },
  charCount: { fontSize: 10, textAlign: 'right', marginTop: 4 },
  empty: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, gap: 12, flex: 1 },
  emptyIconBg: { width: 80, height: 80, borderRadius: 40, alignItems: 'center', justifyContent: 'center' },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 20 },
  msgRow: { flexDirection: 'row', marginBottom: 12, alignItems: 'flex-end' },
  msgRowMe: { justifyContent: 'flex-end' },
  avatar: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginRight: 8 },
  avatarImg: { width: 32, height: 32, borderRadius: 16 },
  avatarText: { fontSize: 11, fontFamily: 'Inter_700Bold' },
  avatarSpacer: { width: 40 },
  bubbleCol: { maxWidth: '78%' },
  bubbleColMe: { alignItems: 'flex-end' },
  senderRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4, marginLeft: 4 },
  senderName: { fontSize: 12, fontFamily: 'Inter_600SemiBold', maxWidth: 160 },
  tierPill: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6 },
  tierPillText: { fontSize: 9, fontFamily: 'Inter_700Bold', color: '#fff' },
  bubble: { borderRadius: 20, paddingHorizontal: 16, paddingVertical: 10, borderWidth: 1, shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.05, shadowRadius: 2, elevation: 1 },
  bubbleMe: { borderTopRightRadius: 6 },
  bubbleOther: { borderTopLeftRadius: 6 },
  mediaImage: { width: 220, height: 140, borderRadius: 12, marginBottom: 6 },
  mediaPlaceholder: { width: 220, height: 140, borderRadius: 12, marginBottom: 6, alignItems: 'center', justifyContent: 'center' },
  mediaPlayText: { fontSize: 12, fontFamily: 'Inter_500Medium', marginTop: 4 },
  attachRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingBottom: 8 },
  attachChip: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 16, borderWidth: 1 },
  attachText: { fontSize: 12, fontFamily: 'Inter_500Medium' },
  // Reply affordances: a quote bar inside the bubble + the chip above the
  // composer while a reply is staged.
  quoteBar: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    borderLeftWidth: 3, paddingLeft: 7, marginBottom: 5, opacity: 0.9,
  },
  quoteText: { fontSize: 11, fontFamily: 'Inter_500Medium', flexShrink: 1 },
  msgFooter: { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 6, marginTop: 2 },
  replyBtn: { padding: 2 },
  replyChip: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 10, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 7, marginBottom: 8,
  },
  replyChipName: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  replyChipPreview: { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 1 },
  msgText: { fontSize: 14, fontFamily: 'Inter_400Regular', lineHeight: 20 },
  timeText: { fontSize: 10, fontFamily: 'Inter_400Regular', marginTop: 4, alignSelf: 'flex-end' },
  inputBar: { borderTopWidth: 1, paddingHorizontal: 12, paddingTop: 10 },
  sendErrorBar: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1 },
  sendErrorText: { flex: 1, fontSize: 11, fontFamily: 'Inter_400Regular' },
  loginPrompt: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12, borderWidth: 1 },
  loginPromptText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
  mediaIcons: { flexDirection: 'row', gap: 2, alignItems: 'flex-end', paddingBottom: 6 },
  mediaBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', position: 'relative' },
  lockBadge: { position: 'absolute', bottom: 4, right: 4, width: 12, height: 12, borderRadius: 6, alignItems: 'center', justifyContent: 'center' },
  inputContainer: { flex: 1, flexDirection: 'row', alignItems: 'center', borderRadius: 22, borderWidth: 1, paddingLeft: 14, paddingRight: 4, paddingVertical: 4, minHeight: 44 },
  textInput: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular', maxHeight: 100, paddingVertical: 6, paddingRight: 6 },
  sendBtn: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center', alignSelf: 'center', flexShrink: 0 },
});
