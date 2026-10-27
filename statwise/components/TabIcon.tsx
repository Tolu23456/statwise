import React, { useEffect } from 'react';
import { StyleProp, ViewStyle } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withSpring,
} from 'react-native-reanimated';
import Ionicons from '@expo/vector-icons/Ionicons';
import type { ComponentProps } from 'react';

type IoniconName = ComponentProps<typeof Ionicons>['name'];

const FOCUS_SPRING = { damping: 14, stiffness: 220, mass: 0.7 };
/** How much the focused icon grows beyond its resting size. */
export const TAB_SCALE_GAIN = 0.16;

/**
 * Tab bar icon with a spring focus animation: switching pages pops the
 * new icon up to +16% scale while the old one settles back — the
 * "changing pages" feedback the tab bar was missing. Purely presentational
 * (state comes from react-navigation's `focused`), runs on the UI thread.
 */
export function TabIcon({
  name,
  color,
  size,
  focused,
  style,
}: {
  name: IoniconName;
  color: string;
  size: number;
  focused: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const progress = useSharedValue(focused ? 1 : 0);

  useEffect(() => {
    progress.value = withSpring(focused ? 1 : 0, FOCUS_SPRING);
  }, [focused, progress]);

  const animated = useAnimatedStyle(() => ({
    transform: [{ scale: 1 + progress.value * TAB_SCALE_GAIN }],
  }));

  return (
    <Animated.View style={[animated, style]}>
      <Ionicons name={name} size={size} color={color} />
    </Animated.View>
  );
}
