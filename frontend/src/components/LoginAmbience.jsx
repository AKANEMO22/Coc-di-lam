
import React from "react";
import { motion, useReducedMotion } from "framer-motion";

export default function LoginAmbience() {
  const reduceMotion = useReducedMotion();

  return (
    <div className="coc-ambience" aria-hidden="true">
      <div className="coc-window-weather">
        <motion.div
          className="coc-cloud coc-cloud-one"
          animate={
            reduceMotion ? {} : { x: [-35, 55, -35] }
          }
          transition={{
            duration: 42,
            repeat: Infinity,
            ease: "easeInOut"
          }}
        />
        <motion.div
          className="coc-cloud coc-cloud-two"
          animate={
            reduceMotion ? {} : { x: [40, -45, 40] }
          }
          transition={{
            duration: 55,
            repeat: Infinity,
            ease: "easeInOut"
          }}
        />
      </div>

      <motion.div
        className="coc-animated-leaves"
        animate={
          reduceMotion ? {} : { rotate: [-2, 2, -2] }
        }
        transition={{
          duration: 5,
          repeat: Infinity,
          ease: "easeInOut"
        }}
      >
        <span>🍃</span>
        <span>🌿</span>
      </motion.div>

      {!reduceMotion &&
        Array.from({ length: 5 }, (_, i) => (
          <motion.span
            key={i}
            className="coc-floating-leaf"
            style={{
              left: `${12 + i * 14}%`,
              top: `${15 + (i % 3) * 12}%`
            }}
            animate={{
              x: [0, 45, 95],
              y: [0, 22, 65],
              rotate: [0, 90, 180],
              opacity: [0, 0.65, 0]
            }}
            transition={{
              duration: 12 + i * 2,
              repeat: Infinity,
              delay: i * 3,
              ease: "linear"
            }}
          >
            🍂
          </motion.span>
        ))}

      <div className="coc-ambient-light" />
    </div>
  );
}
