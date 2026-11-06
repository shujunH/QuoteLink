<template>
  <!-- 三个等轴测货箱组成工作台插画，纯矢量适配窄屏和高分屏。 -->
  <svg viewBox="0 0 370 170" aria-hidden="true" class="hero-art">
    <defs>
      <pattern id="g" width="30" height="17.4" patternUnits="userSpaceOnUse"><path d="M0 0 30 17.4M30 0 0 17.4" fill="none" stroke="#74ac83" stroke-opacity=".22" stroke-width=".7"/></pattern>
      <g id="box"><path d="m0 22 38-22 38 22-38 22Z" fill="#e3edb3"/><path d="m0 22 38 22v40L0 62Z" fill="#a9c686"/><path d="m38 44 38-22v40L38 84Z" fill="#6b9a70"/><path d="m14 14 8-5 38 22-8 5Z" fill="#f8f4d1"/><path d="m52 36 8-5v13l-8 5Z" fill="#b2c78f"/><path d="m7 47 14 8v6L7 53Z" fill="#527953"/></g>
    </defs>
    <path fill="url(#g)" d="M0 0h370v170H0z"/>
    <g fill="none" stroke="#b6d68b" stroke-linecap="round"><path d="m20 132 53 29 48-28m127-107 39-23 45 26" stroke-dasharray="4 6" opacity=".6"/><path d="m104 117 68-39 95 51-64 37Z" fill="#092f28" opacity=".6"/></g>
    <use href="#box" transform="translate(112 62)"/><use href="#box" transform="translate(174 72)"/><use href="#box" transform="translate(146 15)"/>
    <g fill="#c5df9e"><circle cx="68" cy="156" r="3"/><circle cx="288" cy="4" r="2"/><path d="M273 64h8m-4-4v8M76 71h7m-3.5-3.5v7" stroke="#b0cb91" stroke-width="1.2"/></g>
  </svg>
</template>
