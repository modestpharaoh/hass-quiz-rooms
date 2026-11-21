/* Registers the custom `quizrooms:logo` icon with Home Assistant's <ha-icon> system
   so the HA sidebar displays the QuizRooms studio emblem natively. */
(function () {
  // 24x24 geometric vector matching the QuizRooms logo:
  // outer rounded-square frame, Q ring + tail, inner question mark + dot, top-right 4-point spark.
  const QUIZROOMS_LOGO_PATH =
    "M6.2,1.5 H17.8 C20.4,1.5 22.5,3.6 22.5,6.2 V17.8 C22.5,20.4 20.4,22.5 17.8,22.5 H6.2 C3.6,22.5 1.5,20.4 1.5,17.8 V6.2 C1.5,3.6 3.6,1.5 6.2,1.5 Z " +
    "M6.2,3.1 C4.5,3.1 3.1,4.5 3.1,6.2 V17.8 C3.1,19.5 4.5,20.9 6.2,20.9 H17.8 C19.5,20.9 20.9,19.5 20.9,17.8 V6.2 C20.9,4.5 19.5,3.1 17.8,3.1 Z " +
    "M11.2,5.3 C14.5,5.3 17.1,7.9 17.1,11.2 C17.1,12.5 16.7,13.7 16.0,14.7 L18.4,17.1 C18.8,17.5 18.8,18.2 18.4,18.6 C18.0,19.0 17.3,19.0 16.9,18.6 L14.5,16.2 C13.5,16.8 12.4,17.1 11.2,17.1 C7.9,17.1 5.3,14.5 5.3,11.2 C5.3,7.9 7.9,5.3 11.2,5.3 Z " +
    "M11.2,7.3 C9.0,7.3 7.3,9.0 7.3,11.2 C7.3,13.4 9.0,15.1 11.2,15.1 C13.4,15.1 15.1,13.4 15.1,11.2 C15.1,9.0 13.4,7.3 11.2,7.3 Z " +
    "M11.2,8.1 C12.4,8.1 13.2,8.8 13.2,9.7 C13.2,10.5 12.6,11.0 12.0,11.4 C11.7,11.6 11.6,11.8 11.6,12.2 H10.5 C10.5,11.4 10.8,11.0 11.3,10.6 C11.8,10.3 12.0,10.0 12.0,9.7 C12.0,9.3 11.7,9.0 11.2,9.0 C10.7,9.0 10.3,9.3 10.3,9.8 H9.2 C9.2,8.8 10.0,8.1 11.2,8.1 Z " +
    "M11.1,12.9 A0.75,0.75 0 1,0 11.1,14.4 A0.75,0.75 0 1,0 11.1,12.9 Z " +
    "M18.1,4.0 L18.6,5.5 L20.1,6.0 L18.6,6.5 L18.1,8.0 L17.6,6.5 L16.1,6.0 L17.6,5.5 Z";

  window.customIcons = window.customIcons || {};
  window.customIcons.quizrooms = {
    getIcon: async function (_name) {
      return {
        path: QUIZROOMS_LOGO_PATH,
        viewBox: "0 0 24 24",
      };
    },
    getIconList: async function () {
      return [{ name: "logo" }];
    },
  };
})();
