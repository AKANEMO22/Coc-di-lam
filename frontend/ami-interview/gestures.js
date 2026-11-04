'use strict';

const InterviewGestures = (() => {
  const DISAGREE_SECONDS = 2.4;
  function speechMouth(elapsed) {
    return .4 + .55 * (1 - Math.cos(elapsed * Math.PI * 2 * .9)) / 2;
  }
  function disagreementPose(elapsed) {
    if (elapsed <= 0 || elapsed >= DISAGREE_SECONDS) return {yaw: 0, pitch: 0};
    const progress = elapsed / DISAGREE_SECONDS;
    const envelope = Math.sin(Math.PI * progress);
    return {yaw: Math.sin(progress * Math.PI * 4) * 8 * envelope, pitch: -3 * envelope};
  }
  return {DISAGREE_SECONDS, speechMouth, disagreementPose};
})();
if (typeof module !== 'undefined') module.exports = InterviewGestures;
