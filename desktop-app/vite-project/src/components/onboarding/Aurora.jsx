import React from "react";
import "./Aurora.css";

const Aurora = ({ color = "rgba(255, 105, 180, 0.15)", style = {} }) => {
  return (
    <div 
      className="aurora-bg" 
      style={{
        ...style,
      }}
    />
  );
};

export default Aurora;